"""Profile-local settings, private credential storage and operator-defined daily ceilings."""
import json
import os
from pathlib import Path

SECRET_NAMES = {'SESSDATA','BILI_JCT','DEDE_USER_ID','REFRESH_TOKEN','BUVID3','BUVID4'}
LIMITS = {'reply':20, 'private':20, 'dynamic':1, 'proactive':24}
SOCIAL_LIMITS = {'videos_per_round':3, 'comments_daily':6, 'qq_per_group_daily':4, 'qq_cooldown_seconds':3600}

def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)
    path.chmod(0o600)

def _load(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}

class Config(dict):
    def __init__(self, home, overrides=None):
        self.home = Path(home)
        self.data_dir = self.home / 'bilibili'
        self.config_path = self.data_dir / 'settings.json'
        self.secret_path = self.data_dir / 'credentials.json'
        schema = json.loads((Path(__file__).parent/'config_schema.json').read_text(encoding='utf-8-sig'))
        defaults = {k:v.get('default') for k,v in schema.items() if 'default' in v}
        defaults.update(_load(self.config_path))
        defaults.update(_load(self.secret_path))
        defaults.update(overrides or {})
        super().__init__(defaults)
        self.enforce_limits()

    def enforce_limits(self):
        self['ENABLE_PERSONALITY_EVOLUTION'] = False
        self['SHARED_HERMES_MEMORY'] = True
        self['VIDEO_SUBTITLES_ONLY'] = True
        self['ENABLE_AUTONOMOUS_DAILY_PLAN'] = False
        self['PROACTIVE_COIN'] = False
        self['PROACTIVE_FOLLOW'] = False
        self['BEHAVIOR_BUDGET_ENABLED'] = True
        self['DYNAMIC_DAILY_COUNT'] = 1
        self['USE_HERMES_PERSONA'] = True
        self['IMAGE_VISION_PROVIDER_ID'] = 'hermes-vision'
        self['VIDEO_VISION_PROVIDER_ID'] = 'hermes-vision'
        self['LLM_PROVIDER_ID'] = 'hermes'
        for kind, ceiling in LIMITS.items():
            prefix = 'AUTONOMOUS_' + kind.upper() + '_DAILY_'
            self[prefix+'MIN'] = 0
            self[prefix+'MAX'] = ceiling
            self[prefix+'LIMIT'] = ceiling
        self['PROACTIVE_DAILY_LIMIT'] = LIMITS['proactive']
        self['PROACTIVE_COMMENT_DAILY_LIMIT'] = SOCIAL_LIMITS['comments_daily']

    def save_config(self):
        self.enforce_limits()
        secrets = {k:v for k,v in self.items() if k in SECRET_NAMES or k.endswith('API_KEY')}
        _write_private(self.secret_path, secrets)
        _write_private(self.config_path, {k:v for k,v in self.items() if k not in secrets})
