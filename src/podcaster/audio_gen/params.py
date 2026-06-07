from pydantic import BaseModel

class AudioGenParams(BaseModel):
    notebook_id: str
    length: str
