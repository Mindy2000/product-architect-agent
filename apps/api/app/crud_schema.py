"""Shared bounded CRUD contract. No SQL, Python source, filesystem paths or credentials."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CrudField(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    type: Literal['text', 'integer', 'number', 'boolean']
    required: bool = False
    max_length: int = Field(default=2000, ge=1, le=20000)


class CrudEntity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    label: str = Field(min_length=1, max_length=80)
    fields: list[CrudField] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_fields(self):
        names=[field.name for field in self.fields]
        if len(set(names))!=len(names) or any(name in {'id','created_at','updated_at'} or name.startswith('sqlite_') for name in names):
            raise ValueError('字段名重复或使用保留字段。')
        return self


class CrudSchema(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1, le=1000)
    entities: list[CrudEntity] = Field(min_length=1, max_length=10)

    @model_validator(mode='after')
    def unique_entities(self):
        names=[entity.name for entity in self.entities]
        if len(set(names))!=len(names) or any(name.startswith(('sqlite_', 'pa_')) for name in names):
            raise ValueError('数据表名重复或使用保留前缀。')
        return self


def validate_evolution(previous, current):
    new=CrudSchema.model_validate(current)
    if previous is None:
        if new.version!=1:raise ValueError('新应用数据结构必须从版本1开始。')
        return
    old=CrudSchema.model_validate(previous)
    if old.model_dump()==new.model_dump():return
    if new.version!=old.version+1:raise ValueError('数据结构变化时版本必须加1。')
    before={entity.name:entity for entity in old.entities};after={entity.name:entity for entity in new.entities}
    if before.keys()-after.keys():raise ValueError('本阶段不支持自动删除数据表。')
    for name,entity in before.items():
        fields={field.name:field for field in entity.fields};updated={field.name:field for field in after[name].fields}
        if fields.keys()-updated.keys():raise ValueError('本阶段不支持自动删除字段。')
        for key,field in updated.items():
            if key not in fields:
                if field.required:raise ValueError('已有表只允许新增可空字段。')
            elif field.type!=fields[key].type or field.required!=fields[key].required or field.max_length<fields[key].max_length:
                raise ValueError('本阶段不支持自动改字段类型/必填性或缩小长度。')
