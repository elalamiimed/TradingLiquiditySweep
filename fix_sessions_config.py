import yaml

config = {
    'data': {
        'file_path': 'xauusd_1m.csv',
        'warmup_days': 45
    },
    'sessions': {
        'asia_start': 0,
        'asia_end': 7,
        'london_start': 7,
        'london_end': 12,
        'ny_start': 12,
        'ny_end': 21
    },
    'strategy': {
        'ema_period': 200,
        'atr_period': 14,
        'atr_multiplier': 1.5,
        'rr_ratio': 1.0,
        'risk_pct': 0.02,
        'structure_lookback': 3,
        'sweep_expiry_bars': 3
    },
    'risk': {
        'risk_pct': 0.02,
        'atr_multiplier': 1.5,
        'rr_ratio': 1.0
    },
    'execution': {
        'commission': 0.0002,
        'slippage_fixed': 0.05,
        'spread_buffer': 0.10,
        'initial_cash': 100000.0
    },
    'logging': {
        'level': 'INFO',
        'directory': 'logs'
    }
}

with open('configs/config.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

print("Config written successfully!")
# Verify
with open('configs/config.yaml', 'r') as f:
    print(f.read())
