# models.py -> step1_extract_all.py

import os
import torch
from transformers import (
    LlavaForConditionalGeneration,
    AutoTokenizer,
    AutoModelForCausalLM,
    AutoProcessor,
    PaliGemmaForConditionalGeneration,
)
from sae_lens import SAE
import logging
from typing import Tuple, Literal, Union, Optional
from pathlib import Path
from utils_neuronpedia import FeatureLookup

device = "cuda" if torch.cuda.is_available() else "cpu"

def gather_activations(model, target_layer, inputs, module_type="res"):
    target_act = None

    def gather_target_act_hook(mod, inp, out):
        nonlocal target_act
        # out might be a tuple if the module returns multiple values
        # Adjust indexing if needed based on module outputs
        if isinstance(out, tuple):
            target_act = out[0]
        else:
            target_act = out
        return out

    # Choose which module to hook based on `module_type`
    if module_type == "res":
        module_to_hook = model.model.layers[target_layer]
    elif module_type == "att":
        module_to_hook = model.model.layers[target_layer].self_attn
    elif module_type == "mlp":
        module_to_hook = model.model.layers[target_layer].mlp
    else:
        raise ValueError(f"Unknown module_type: {module_type}")

    handle = module_to_hook.register_forward_hook(gather_target_act_hook)

    with torch.no_grad():
        _ = model(inputs)

    handle.remove()
    return target_act

def load_models_and_tokenizer(
    checkpoint: str,
    layer: int,
    device: str,
    model_type: Literal["llm", "vlm"],
    sae_location: str,
    width: str,
) -> Tuple[
    Union[AutoModelForCausalLM, LlavaForConditionalGeneration],
    Union[AutoTokenizer, AutoProcessor],
    "SAE",
]:
    """
    Load model, tokenizer/processor, and SAE for a given checkpoint.

    Args:
        checkpoint: Model checkpoint name/path
        layer: Layer number to extract features from
        device: Device to load models on
        model_type: Type of model ('llm' or 'vlm')
        sae_location: SAE release type ('res' or 'mlp')

    Returns:
        Tuple of (model, tokenizer/processor, SAE)
    """
    if model_type == "llm":
        logging.info(f"Loading LLM model ++ {checkpoint}")
        tokenizer = AutoTokenizer.from_pretrained(checkpoint)
        model = AutoModelForCausalLM.from_pretrained(
            checkpoint, device_map="auto", torch_dtype=torch.float16
        ) #sequential
    elif model_type == "vlm":
        logging.info(f"Loading VLM model ++ {checkpoint}")
        processor = AutoProcessor.from_pretrained(checkpoint)
        if "paligemma" in checkpoint.lower():
            model = PaliGemmaForConditionalGeneration.from_pretrained(
                checkpoint, device_map="auto"
            )
        else:
            model = LlavaForConditionalGeneration.from_pretrained(
                checkpoint, device_map="auto"
            )
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")

    if model_type == "llm" and "gemma-2" in checkpoint.lower():
        # Get SAE configuration based on model architecture
        sae_location, feature_id, _ = get_sae_config(
            checkpoint, layer, sae_location, width
        )

        logging.info(
            f"Loading SAE model from release {sae_location}, feature {feature_id}"
        )
        sae, _, _ = SAE.from_pretrained(
            release=f"{sae_location}",
            sae_id=feature_id,
            device=device,
        )
    elif model_type == "vlm" and "paligemma2" in checkpoint.lower():
        sae_location, feature_id, _ = get_sae_config(
            checkpoint, layer, sae_location, width
        )

        logging.info(
            f"Loading SAE model from release {sae_location}, feature {feature_id}"
        )
        sae, _, _ = SAE.from_pretrained(
            release=f"{sae_location}",
            sae_id=feature_id,
            device=device,
        )

    else:
        if "it" in checkpoint.lower():
            sae_release = "gemma-2b-it"
        else:
            sae_release = "gemma-2b"
        logging.info(f"Loading SAE model from release {sae_release}, layer {layer}")
        sae, _, _ = SAE.from_pretrained(
            release=f"{sae_release}-res-jb",
            sae_id=f"blocks.{layer}.hook_resid_post",
            device=device,
        )
    sae.to(device)
    if model_type == "llm":
        return model, tokenizer, sae
    else:
        return model, processor, sae


def get_sae_config(
    model_name: str, layer: int, sae_location: str, width: str
) -> Tuple[str, str, Optional[Path]]:
    """
    Generate SAE release name, feature ID, and path to feature explanations.
    """
    model_name = model_name.lower()
    if "paligemma2-3b" in model_name:
        model_name = "google/gemma-2-2b"
    elif "paligemma2-10b" in model_name:
        model_name = "google/gemma-2-9b"
    elif "paligemma2-10b-it" in model_name:
        ## this is not right
        model_name = "google/gemma-2-9b-it"

    # Configuration dictionary for different model architectures
    model_configs = {
        "google/gemma-2b": {
            "release_template": "gemma-2b-{}-jb",
            "id_template": "blocks.{}.hook_resid_post",  # Changed this - no width needed
            "supported_widths": ["width_16k"],
        },
        "google/gemma-2-2b": {
            "release_template": "gemma-scope-2b-pt-{}-canonical",
            "id_template": "layer_{}/{}/canonical",
            "supported_widths": ["width_16k", "width_65k", "width_1m"],
        },
        "google/gemma-2-9b": {
            "release_template": "gemma-scope-9b-pt-{}-canonical",
            "id_template": "layer_{}/{}/canonical",
            "supported_widths": ["width_16k", "width_131k", "width_1m"],
        },
        "google/gemma-2-9b-it": {
            "release_template": "gemma-scope-9b-it-{}-canonical",
            "id_template": "layer_{}/{}/canonical",
            "supported_widths": ["width_16k", "width_131k", "width_1m"],
        },
    }

    # Determine which model architecture we're working with
    model_arch = next((arch for arch in model_configs if arch in model_name), None)
    if not model_arch:
        raise ValueError(f"Unsupported model architecture in checkpoint: {model_name}")

    config = model_configs[model_arch]

    # Validate width format
    if not width.startswith("width_"):
        width = f"width_{width}"

    if width not in config["supported_widths"]:
        raise ValueError(
            f"Unsupported width '{width}' for model {model_name}. "
            f"Supported widths are: {', '.join(config['supported_widths'])}"
        )

    # Generate release name and feature ID
    feature_type = "res" if "res" in sae_location else "mlp"
    sae_location = config["release_template"].format(feature_type)

    # Different feature ID generation based on model type
    if model_arch == "google/gemma-2b":
        feature_id = config["id_template"].format(
            layer
        )  # Don't include width for original gemma-2b
    else:
        feature_id = config["id_template"].format(
            layer, width
        )  # Include width for newer models

    # Rest of the function remains the same...
    explanation_file_path = None
    try:
        feature_lookup = FeatureLookup(
            config_path="src/resource/pretrained_saes.yaml",
            cache_dir="./explanation_cache",
        )
    except Exception as e:
        logging.error(f"Error fetching explanations: {str(e)}")
        explanation_file_path = None
        pass

    return sae_location, feature_id, explanation_file_path


def prepare_inputs(images, texts, tokenizer_or_processor, device, model_type):
    if model_type == "llm":
        if texts is None:
            raise ValueError("Texts are required for LLM models.")

        # Flatten the nested list structure
        if isinstance(texts, list):
            # Flatten any nested lists and join with space
            texts = [t[0] if isinstance(t, list) else str(t) for t in texts]

        # Ensure all elements are strings
        texts = [str(t) if t is not None else "" for t in texts]

        logging.debug(f"Processed texts sample: {texts[:2]}")  # Log first two examples

        inputs = tokenizer_or_processor(
            texts, return_tensors="pt", padding=True, truncation=True
        )
        return inputs
    elif model_type == "vlm":
        if images is None:
            raise ValueError("Images are required for VLM models.")
        if texts is None:
            texts = [""] * len(images)
        return prepare_image_inputs(images, texts, tokenizer_or_processor, device)
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")


def prepare_image_inputs(images, texts, processor, device):
    if not isinstance(texts, list):
        texts = [texts] * len(images)
    prompts = []
    if "Gemma" in processor.tokenizer_class[0]:
        inputs = processor(
        text=texts, images=list(images), return_tensors="pt", padding=True
            ).to(torch.bfloat16)
        return inputs
    else:
        for text in texts:
            sanitized_text = text.strip().replace("\n", " ")
            prompt = processor.tokenizer.apply_chat_template(
                [{"role": "user", "content": "<image>" + sanitized_text}],
                tokenize=False,
                add_generation_prompt=True,
            )
            prompts.append(prompt)
        inputs = processor(
            text=prompts, images=list(images), return_tensors="pt", padding=True
        )
    return inputs


def extract_hidden_states(model, inputs, layer, model_type, sae_location):
    try:
        # Check the number of available GPUs
        num_gpus = len(os.environ["CUDA_VISIBLE_DEVICES"].split(","))

        # If there's only one GPU, ensure inputs are moved onto it
        if num_gpus < 2:
            inputs = {k: v.to("cuda") for k, v in inputs.items()}

            if model_type == "llm":
                # For LLM models on a single GPU, use gather_activations
                # TODO: GPU bug on gather_activations for multiple GPUs
                target_act = gather_activations(model, layer, inputs['input_ids'], module_type=sae_location)
            else:
                # For VLM models, only 'res' is supported currently
                if sae_location != "res":
                    raise ValueError("Only 'res' SAE location is supported for VLM models at this moment.")

                outputs = model.generate(
                    **inputs,
                    max_new_tokens=1,
                    return_dict_in_generate=True,
                    output_scores=True,
                    output_hidden_states=True,
                )
                hidden_states = outputs.get("hidden_states", None)
                if hidden_states is None:
                    raise ValueError("Hidden states not found in outputs")
                target_act = hidden_states[0][layer + 1]

        else:
            # For multiple GPUs
            if model_type == "llm":
                outputs = model(**inputs, output_hidden_states=True, return_dict=True, use_cache=False)
                hidden_states = outputs.hidden_states
                target_act = hidden_states[layer + 1]
            else:
                if sae_location != "res":
                    raise ValueError("Only 'res' SAE location is supported for VLM models at this moment.")

                outputs = model.generate(
                    **inputs,
                    max_new_tokens=1,
                    return_dict_in_generate=True,
                    output_scores=True,
                    output_hidden_states=True,
                )
                hidden_states = outputs.get("hidden_states", None)
                if hidden_states is None:
                    raise ValueError("Hidden states not found in outputs")
                target_act = hidden_states[0][layer + 1]

        return target_act

    except RuntimeError as e:
        error_str = str(e)
        if ("Expected all tensors to be on the same device" in error_str or
            "indices should be either on cpu or on the same device" in error_str):
            print(f"Device mismatch detected: {e}")
        raise



def analyze_with_sae(sae, target_act):
    sae_acts = sae.encode(target_act.to(torch.float32))
    recon = sae.decode(sae_acts)
    mse = torch.mean((recon - target_act.to(torch.float32)) ** 2)
    var = torch.var(target_act.to(torch.float32))
    reconstruction_error = 1 - mse / var if var > 0 else torch.tensor(0.0)
    return sae_acts, reconstruction_error
