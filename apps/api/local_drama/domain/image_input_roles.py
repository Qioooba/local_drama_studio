"""Scalar image slots shared by generation contracts and Comfy materialization."""

IDENTITY_REFERENCE_ROLES = ("REFERENCE_IMAGE_1", "REFERENCE_IMAGE_2", "REFERENCE_IMAGE_3")
COMFY_IMAGE_INPUT_ROLES = frozenset({
    "FIRST_FRAME", "END_FRAME", "MIDDLE_KEYFRAME", "REFERENCE_IMAGE",
    *IDENTITY_REFERENCE_ROLES,
})
