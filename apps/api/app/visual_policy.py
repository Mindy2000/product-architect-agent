"""Immutable settings for deterministic local screenshot evidence."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
class Rect(BaseModel):
    model_config=ConfigDict(extra='forbid')
    x:int=Field(ge=0,le=1000)
    y:int=Field(ge=0,le=844)
    width:int=Field(ge=1,le=1000)
    height:int=Field(ge=1,le=844)
class VisualPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=False
    viewport:Literal['desktop','mobile']='desktop'
    storage:dict[str,str]=Field(default_factory=dict,max_length=32)
    api_fixtures:dict[str,str]=Field(default_factory=dict,max_length=20)
    masks:list[Rect]=Field(default_factory=list,max_length=12)
    region:Rect|None=None
    @model_validator(mode='after')
    def bounded(self):
        import json
        if len(json.dumps(self.storage))+len(json.dumps(self.api_fixtures))>16384:raise ValueError('合成数据最多16KiB')
        for key,value in self.api_fixtures.items():
            if not key.startswith('/api/') or len(key)>200 or '#' in key:raise ValueError('API样本须使用本地 /api/ 路径')
            json.loads(value)
        w,h=(390,844) if self.viewport=='mobile' else (1000,720)
        for r in [*self.masks,*([self.region] if self.region else [])]:
            if r.x+r.width>w or r.y+r.height>h:raise ValueError('遮罩/比较区域超出所选视口')
        if not self.enabled and (self.masks or self.region or self.storage or self.api_fixtures):raise ValueError('固定数据和区域设置需要开启独立视觉模式')
        return self
