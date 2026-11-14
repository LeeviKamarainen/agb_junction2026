# /// script
# requires-python = ">=3.11"
# dependencies = ["accelerate", "pillow", "torch", "torchvision", "transformers"]
# ///
"""VEC-1 round trip: does Gemma 4 answer from image vectors read back from disk?

Run on a GPU machine (E2B needs about 12 GB free), from anywhere:

    uv run scripts/vec1_roundtrip.py --image still.jpg

Prints three answers to the same question:

    A  the normal way, from the image
    C  from a blank image of the same size (the control)
    B  from the image's vectors, saved to disk and read back, with the blank image only
       supplying the placeholder tokens

It works if B says the same as A and C does not.
"""

import argparse
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForMultimodalLM, AutoProcessor
from transformers.modeling_outputs import BaseModelOutputWithPooling

PIXEL_KEYS = ("pixel_values", "image_position_ids")


def build_inputs(processor, image, question, tokens, device):
    content = [{"type": "image", "image": image}, {"type": "text", "text": question}]
    extra = {"max_soft_tokens": tokens} if tokens else {}
    return processor.apply_chat_template(
        [{"role": "user", "content": content}],
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
        enable_thinking=False,
        **extra,
    ).to(device)


def answer(model, processor, inputs, **extra):
    out = model.generate(**inputs, **extra, max_new_tokens=128, do_sample=False)
    new_tokens = out[0][inputs["input_ids"].shape[-1] :]
    return processor.decode(new_tokens, skip_special_tokens=True).strip()


def main():
    parser = argparse.ArgumentParser(description="Gemma 4 answering from stored image vectors.")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--model", default="google/gemma-4-E2B-it")
    parser.add_argument("--question", default="Describe what is in this image in one sentence.")
    parser.add_argument(
        "--tokens",
        type=int,
        choices=[70, 140, 280, 560, 1120],
        help="soft tokens per image (default: the model's own, 280)",
    )
    parser.add_argument("--out", type=Path, default=Path("vec1_vectors.pt"))
    args = parser.parse_args()

    processor = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForMultimodalLM.from_pretrained(args.model, dtype="auto", device_map="auto")

    image = Image.open(args.image).convert("RGB")
    inputs = build_inputs(processor, image, args.question, args.tokens, model.device)
    print("A (image):  ", answer(model, processor, inputs))

    # Phase 1: the vision encoder and projector alone; only their output is kept.
    with torch.no_grad():
        features = model.get_image_features(*(inputs[k] for k in PIXEL_KEYS), return_dict=True)
    torch.save({"vectors": [v.cpu() for v in features.pooler_output], "size": image.size}, args.out)
    del features, inputs, image

    # Phase 2: the language model, with no access to the image.
    saved = torch.load(args.out)
    blank = Image.new("RGB", saved["size"])
    inputs = build_inputs(processor, blank, args.question, args.tokens, model.device)
    print("C (blank):  ", answer(model, processor, inputs))

    text_only = {k: v for k, v in inputs.items() if k not in PIXEL_KEYS}
    stored = BaseModelOutputWithPooling(
        pooler_output=[v.to(model.device) for v in saved["vectors"]]
    )
    print("B (vectors):", answer(model, processor, text_only, mm_encoder_outputs={"image": stored}))

    first = saved["vectors"][0]
    kb = sum(v.numel() * v.element_size() for v in saved["vectors"]) / 1024
    print(f"{first.shape[0]} tokens x {first.shape[1]} wide = {kb:.0f} KB on disk ({first.dtype})")


if __name__ == "__main__":
    main()
