import os

import numpy as np
import PIL.Image
import torch
import json
import dnnlib
import legacy
import torchvision.models as md


def disable_gradient_flow_for_model(model:torch.nn.Module, device):
    model.eval().requires_grad_(False)
    model.to(device)


def prepare_styleganxl_generator_discriminator(network_pkl, device):
    print('Loading networks from "%s"...' % network_pkl)
    with dnnlib.util.open_url(network_pkl) as f:
        networks = legacy.load_network_pkl(f)
        discriminator = networks['D']
        generator = networks['G_ema']
        discriminator = discriminator.eval().requires_grad_(False).to(device)
        generator = generator.eval().requires_grad_(False).to(device)
    return generator, discriminator

def remove_tf_diff_discriminator(discriminator):
    name_of_tf_efficientnet = 'tf_efficientnet_lite0'

    discriminator.diffaug = False
    del discriminator.feature_networks[name_of_tf_efficientnet]
    discriminator.backbones = discriminator.backbones[1]
    del discriminator.discriminators[name_of_tf_efficientnet]  
    return discriminator

def discriminators_on_device(discriminator,device):
    name_of_deit_model = "deit_base_distilled_patch16_224"

    for k, disc in discriminator.discriminators[name_of_deit_model].mini_discs.items():
        disable_gradient_flow_for_model(disc,device)
        discriminator.discriminators[name_of_deit_model].mini_discs[k] = disc

    disable_gradient_flow_for_model(discriminator.feature_networks[name_of_deit_model].pretrained,device)

    disable_gradient_flow_for_model(discriminator.feature_networks[name_of_deit_model].scratch,device)

    for k, disc in discriminator.feature_networks[name_of_deit_model].pretrained.activations.items():
        disc = disc.requires_grad_(False)
        discriminator.feature_networks[name_of_deit_model].pretrained.activations[k] = disc.to(device)
    return discriminator


def yield_individual_class_folder_with_imagenet_images(data_location, start_folder_class_index, end_folder_class_index):
    g = os.walk(data_location)
    pth,folders,_ = next(g)
    folders_considered = folders[start_folder_class_index:end_folder_class_index]
    return pth, folders_considered

def get_unique_imagenet_image_name_and_skip_generated_content(consider_this_folder):
    files = next(os.walk(consider_this_folder))[2]
    files.sort()
    ids = list(set([elem.split(".")[0].split("_")[1] for elem in files]))
    return ids

def load_target_image(target_fname, img_resolution):
    target_pil = PIL.Image.open(target_fname+".JPEG").convert('RGB')
    w, h = target_pil.size
    s = min(w, h)
    target_pil = target_pil.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    target_pil = target_pil.resize((img_resolution, img_resolution), PIL.Image.LANCZOS)
    target_uint8 = np.array(target_pil, dtype=np.uint8)
    return target_pil, target_uint8

def synthesize_and_save_image(generator, style_code, save_name):
    synth_image = generator.synthesis(style_code.repeat(1, generator.num_ws, 1))
    synth_image = (synth_image + 1) * (255/2)
    synth_image = synth_image.permute(0, 2, 3, 1).clamp(0, 255).to(torch.uint8)[0].cpu().numpy()
    PIL.Image.fromarray(synth_image, 'RGB').save(save_name)

def prepare_classifier(classifier_object, device):
    if classifier_object:
        disable_gradient_flow_for_model(classifier_object,device)
        return classifier_object
    else:
        return prepare_prime_resnet50_classifier_for_fooling(device, "ResNet50_ImageNet_PRIME_noJSD.ckpt")

def prepare_prime_resnet50_classifier_for_fooling(device, perceptor_path):
    perceptor = md.resnet50(pretrained=True)
    perceptor.eval().requires_grad_(False)
    base_model=torch.load(perceptor_path)
    perceptor.load_state_dict(base_model)
    disable_gradient_flow_for_model(perceptor,device)
    return perceptor

def prepare_discriminator_generator(device):

    network_pkl = "https://s3.eu-central-1.amazonaws.com/avg-projects/stylegan_xl/models/imagenet256.pkl"
    generator, discriminator = prepare_styleganxl_generator_discriminator(network_pkl, device)

    disable_gradient_flow_for_model(generator, device)
    disable_gradient_flow_for_model(discriminator, device)

    discriminator = remove_tf_diff_discriminator(discriminator)
    discriminator = discriminators_on_device(discriminator,device)

    return generator, discriminator

def get_imagenet_classname_to_class_mapping():

    f= open("imagenet_class_index.json")
    idxs = json.load(f)
    inv_map = {v[0]: int(k) for k, v in idxs.items()}
    return inv_map

def prepare_init_latent_optimization_input(target_fname, inv_map, folder, num_classes_in_dataset, device):
    path = target_fname + "_projected_w.npz"
    path_exist = os.path.exists(path)
    if path_exist:
        w_init = path
    else:
        w_init = False

    target_class = inv_map[folder]
    c_samples = torch.tensor(np.zeros([1, num_classes_in_dataset], dtype=np.float32)).to(device)
    c_samples[:,target_class]=1

    return w_init, target_class, c_samples