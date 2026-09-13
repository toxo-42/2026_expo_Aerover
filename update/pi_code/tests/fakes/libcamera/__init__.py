class Transform:
    def __init__(self, hflip: int = 0, vflip: int = 0) -> None:
        self.hflip, self.vflip = hflip, vflip

    def __eq__(self, other) -> bool:
        return isinstance(other, Transform) and (self.hflip, self.vflip) == (other.hflip, other.vflip)

    def __repr__(self) -> str:
        return f"Transform(hflip={self.hflip}, vflip={self.vflip})"
