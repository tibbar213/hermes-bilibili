"""Profile-local settings, private credential storage and operator-defined daily ceilings."""
import json
import os
from pathlib import Path

SECRET_NAMES = {'SESSDATA','BILI_JCT','DEDE_USER_ID','REFRESH_TOKEN','BUVID3','BUVID4'}
LIMITS = {'reply':20, 'private':20, 'dynamic':1, 'proactive':24}
SOCIAL_LIMITS = {'videos_per_round':3, 'comments_daily':6, 'qq_per_group_daily':4, 'qq_cooldown_seconds':3600}

def positive_limit(value, default):
    try:
        number = int(value)
        if isinstance(value, bool) or number < 1:
            raise ValueError('Ceilings must be positive integers')
        return number
    except (TypeError, ValueError, OverflowError):
        return default


def effective_limits(config):
    daily = {k:positive_limit(config.get('AUTONOMOUS_'+k.upper()+'_DAILY_LIMIT'),v) for k,v in LIMITS.items()}
    social = {k:positive_limit(config.get('NATIVE_'+k.upper()),v) for k,v in SOCIAL_LIMITS.items()}
    social['comments_daily'] = positive_limit(config.get('PROACTIVE_COMMENT_DAILY_LIMIT'),social['comments_daily'])
    return daily, social


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
        explicit = _load(self.config_path)
        explicit.update(_load(self.secret_path))
        explicit.update(overrides or {})
        defaults.update(explicit)
        # Schema defaults are legacy; only explicit operator settings override
        # native defaults. Positive ceilings cannot silently become unlimited.
        for kind, ceiling in LIMITS.items():
            key = 'AUTONOMOUS_' + kind.upper() + '_DAILY_LIMIT'
            defaults[key] = positive_limit(explicit.get(key), ceiling)
        for name, ceiling in SOCIAL_LIMITS.items():
            key = 'NATIVE_' + name.upper()
            defaults[key] = positive_limit(explicit.get(key), ceiling)
        defaults['PROACTIVE_COMMENT_DAILY_LIMIT'] = positive_limit(explicit.get('PROACTIVE_COMMENT_DAILY_LIMIT'),defaults['NATIVE_COMMENTS_DAILY'])
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
        self['USE_HERMES_PERSONA'] = True
        self['IMAGE_VISION_PROVIDER_ID'] = 'hermes-vision'
        self['VIDEO_VISION_PROVIDER_ID'] = 'hermes-vision'
        self['LLM_PROVIDER_ID'] = 'hermes'
        for kind, default in LIMITS.items():
            ceiling = positive_limit(self.get('AUTONOMOUS_'+kind.upper()+'_DAILY_LIMIT'),default)
            prefix = 'AUTONOMOUS_' + kind.upper() + '_DAILY_'
            self[prefix+'MIN'] = 0
            self[prefix+'MAX'] = ceiling
            self[prefix+'LIMIT'] = ceiling
        self['DYNAMIC_DAILY_COUNT'] = self['AUTONOMOUS_DYNAMIC_DAILY_LIMIT']
        for name, default in SOCIAL_LIMITS.items():
            key = 'NATIVE_' + name.upper()
            self[key] = positive_limit(self.get(key),default)
        self['PROACTIVE_DAILY_LIMIT'] = self['AUTONOMOUS_PROACTIVE_DAILY_LIMIT']
        self['PROACTIVE_COMMENT_DAILY_LIMIT'] = positive_limit(self.get('PROACTIVE_COMMENT_DAILY_LIMIT'),self['NATIVE_COMMENTS_DAILY'])
        self['NATIVE_COMMENTS_DAILY'] = self['PROACTIVE_COMMENT_DAILY_LIMIT']

    def save_config(self):
        self.enforce_limits()
        secrets = {k:v for k,v in self.items() if k in SECRET_NAMES or k.endswith('API_KEY')}
        _write_private(self.secret_path, secrets)
        _write_private(self.config_path, {k:v for k,v in self.items() if k not in secrets})
