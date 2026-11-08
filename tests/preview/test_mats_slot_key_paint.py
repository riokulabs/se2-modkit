"""An SDK material painted through a constant color mask keys its preview by paint, like one with a mask map."""
from se2modkit.preview import mats


class _Mat:
    def __init__(self, mask_path=None, mask_value=0.0):
        self._p, self._v = mask_path, mask_value

    def path(self, role):
        return self._p if role == "colormask" else None

    def value(self, role, default):
        return self._v if role == "colormask" else default


def test_constant_color_mask_is_paintable():
    assert mats._paintable(_Mat(mask_value=1.0))
    assert mats._paintable(_Mat(mask_path="x_colormask.png"))
    assert not mats._paintable(_Mat())
