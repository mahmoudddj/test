"""XNAT integration layer (optional; enabled with XNAT_ENABLED=true).

All XNAT REST specifics are confined to this package. The rest of the platform talks to
``irtcri.imaging.repository.ImagingService`` and never to XNAT directly.

REST calls follow the documented XNAT REST API (``/data/projects/.../subjects/.../experiments/...``).
They MUST be verified against the study's XNAT version with the optional live test-suite
(``tests/integration/test_xnat_live.py``) before production use.
"""
