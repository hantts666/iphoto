"""One exact, bounded neural result inside a manual channel session."""
from hashlib import sha256

import numpy as np

MAX_PIXELS = 8_000_000


class ChannelSolver:
    def __init__(self, solve=None):
        if solve is None:
            from .neural import solve
        self.solve = solve
        self._entry = None
        self.reused = False
        self.computed_tiles = 0

    def __call__(self, image, trimap, *, progress=None):
        self.reused, self.computed_tiles = False, 0
        key = None
        if (image.mode == 'RGB' and image.width*image.height <= MAX_PIXELS
                and isinstance(trimap,np.ndarray) and trimap.dtype == np.uint8
                and trimap.shape == (image.height,image.width)):
            digest = sha256(image.tobytes())
            digest.update(trimap.tobytes())
            key = image.size, digest.digest()
        if key is not None and self._entry is not None and self._entry[0] == key:
            self.reused = True
            if progress is not None:
                progress(phase='reuse')
            return self._entry[1].copy(), self._entry[2]
        alpha, tiles = self.solve(image,trimap,progress=progress)
        self.computed_tiles = tiles
        # Never retain the RGB, trimap or caller-owned output. A different
        # image/guide evicts the sole <=8MB entry; oversized work is not cached.
        self._entry = None if key is None else (key,alpha.copy(),tiles)
        return alpha, tiles
