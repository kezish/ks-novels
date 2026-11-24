from .jiujiu import Jiujiu
from .shuku52 import Shuku52
def registry():
    return {x.source_id:x for x in (Jiujiu(),Shuku52())}
