"""Grad-CAM for CNN image encoders (thermal / RGB branches).

Research aid only: a Grad-CAM map shows image regions that most influenced the
model's output for this input. It does NOT demonstrate clinical reasoning or
causality and must not be used to localise infection.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F

CAVEAT = ("Grad-CAM highlights regions associated with the model output for this image; it is not evidence of "
          "causal or clinical reasoning.")


def grad_cam(model: torch.nn.Module, batch: dict, branch: str = "thermal_enc") -> np.ndarray:
    """Return a [0,1] heatmap (H x W of the branch input) for a single-sample batch."""
    enc = getattr(model, branch)
    layer = getattr(enc, "cam_layer", None)
    if layer is None:
        raise NotImplementedError(f"Grad-CAM is not defined for backbone of {branch} (e.g. ViT)")
    acts, grads = {}, {}

    def fwd_hook(_, __, out):
        acts["a"] = out

    def bwd_hook(_, __, gout):
        grads["g"] = gout[0]

    h1 = layer.register_forward_hook(fwd_hook)
    h2 = layer.register_full_backward_hook(bwd_hook)
    try:
        model.eval()
        model.zero_grad(set_to_none=True)
        inp_key = "thermal" if branch == "thermal_enc" else "rgb"
        batch = {k: (v.clone().requires_grad_(k == inp_key) if torch.is_floating_point(v) else v)
                 for k, v in batch.items()}
        logit = model(batch)
        logit.sum().backward()
        a, g = acts["a"], grads["g"]
        w = g.mean(dim=(2, 3), keepdim=True)
        cam = F.relu((w * a).sum(dim=1, keepdim=True))
        cam = F.interpolate(cam, size=batch[inp_key].shape[-2:], mode="bilinear", align_corners=False)[0, 0]
        cam = cam.detach().cpu().numpy()
        if cam.max() > 0:
            cam = cam / cam.max()
        return cam.astype(np.float32)
    finally:
        h1.remove()
        h2.remove()
