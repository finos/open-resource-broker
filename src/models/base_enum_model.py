from enum import Enum

class BaseEnumModel(Enum):
    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        raise ValueError(f"Cannot create {cls.__name__} from {value}")

    def to_dict(self):
        return self.value

    def __str__(self):
        return self.value

    def __repr__(self):
        return f"{self.__class__.__name__}.{self.name}"
