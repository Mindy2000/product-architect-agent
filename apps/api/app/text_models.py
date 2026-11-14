"""Explicit text routes, rates and role selection; no discovery or network calls."""
import os
import re
from typing import Literal

TextModel = Literal['deepseek-flash', 'deepseek-v4-pro', 'qwen3.8-flash']
MODELS = {
    'deepseek-flash': {'provider': 'deepseek', 'rates_cny': (2.4, 9.6), 'minimum': .25},
    'deepseek-v4-pro': {'provider': 'deepseek', 'rates_cny': (10.56, 31.68), 'minimum': .35},
    'qwen3.8-flash': {'provider': 'aliyun_text', 'rates_cny': (.8, 2.7), 'minimum': .25},
}
ROLES = ('planner', 'developer', 'reviewer')
QWEN_BASE = 'https://dashscope.aliyuncs.com/compatible-mode/v1'


def valid_qwen_base(value):
    # This adapter/rate card is Beijing only. Never send credentials to a custom proxy.
    return value == QWEN_BASE or bool(re.fullmatch(r'https://[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com/compatible-mode/v1', value))


def role_model(state, role):
    return (state.get('role_models') or {}).get(role) or state.get('model_name') or 'deepseek-flash'


def frozen_roles(default, overrides):
    return {role: overrides.get(role) or default for role in ROLES}


def route(selected, base_settings):
    if selected not in MODELS:raise ValueError('文字模型不在允许列表中。')
    result = {**base_settings, 'model': selected}
    if MODELS[selected]['provider'] == 'deepseek':
        if result['provider'] != 'deepseek':raise ValueError('DeepSeek模型须使用DeepSeek通道配置。')
    else:
        reuse = os.getenv('QWEN_TEXT_USE_VISION_KEY', 'false').lower() == 'true'
        if reuse and not valid_qwen_base(os.getenv('VISION_BASE_URL',QWEN_BASE).strip().rstrip('/')):raise ValueError('复用的视觉密钥接口不是北京地域；请单独配置文字通道。')
        result.update(provider='aliyun_text',
                      enabled=result['enabled'] and os.getenv('QWEN_TEXT_CALLS_ENABLED', 'false').lower() == 'true',
                      api_key=(os.getenv('VISION_API_KEY', '') if reuse else os.getenv('QWEN_TEXT_API_KEY', '')).strip(),
                      base_url=os.getenv('QWEN_TEXT_BASE_URL', QWEN_BASE).strip().rstrip('/'))
        if not valid_qwen_base(result['base_url']):raise ValueError('千问文字当前只支持北京官方接口；其他地域须先核对权限与费率。')
    return result


def catalog(settings):
    result=[]
    for name, spec in MODELS.items():
        try:
            value=route(name,settings)
            result.append({'id':name,'provider':spec['provider'],'configured':bool(value['api_key']),
                           'enabled':value['enabled'],'region':'beijing' if spec['provider']=='aliyun_text' else None})
        except ValueError:
            result.append({'id':name,'provider':spec['provider'],'configured':False,'enabled':False})
    return result
