class Vector:
    """3D Vector"""
    __slots__ = ('x', 'y', 'z')
    
    def __init__(self, x, y, z=None):
        self.x = x
        self.y = y
        self.z = z
    
    def __iter__(self):
        yield self.x
        yield self.y
        if self.z is not None:
            yield self.z


class Hash:
    """Hash utilities for League of Legends"""
    
    @staticmethod
    def elf(s):
        """ELF hash used for bone names"""
        s = s.lower()
        h = 0
        for c in s:
            h = (h << 4) + ord(c)
            t = (h & 0xF0000000)
            if t != 0:
                h ^= (t >> 24)
            h &= ~t
        return h & 0xFFFFFFFF
