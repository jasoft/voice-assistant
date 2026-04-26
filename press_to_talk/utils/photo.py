import os
from typing import Optional

def get_photo_url(photo_path: Optional[str]) -> Optional[str]:
    """Convert database photo path to web accessible URL."""
    if not photo_path:
        return None
    # photo_path is typically "photos/filename.jpg"
    # we want to map it to "/assets/filename.jpg"
    filename = os.path.basename(photo_path)
    return f"/assets/{filename}"
