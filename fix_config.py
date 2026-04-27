import yaml

config = {
    'data': {
        'file_path': 'xauusd_1m.csv',
        'warmup_days': 45
    },
    'sessions': {
        'asia': {'start': '00:00', 'end': '08:00'},
        'london': {'start': '07:00', 'end': '16:00'},
        'ny': {'start': '13:00', 'end': '22:00'}
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
        'risk_per_trade_pct': 0.02,
        'sl_atr_multiplier': 1.5,
        'tp_rr_ratio': 1.0
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
    },
    'trading': {
        'symbols': ['XAUUSD'],
        'timeframes': ['4h', '1h', '5m', '1m']
    },
    'api': {
        'mt5_login': 0,
        'mt5_password': '',
        'mt5_server': '',
        'news_api_url': 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
    }
}

with open('configs/config.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

print("Config updated successfully!")