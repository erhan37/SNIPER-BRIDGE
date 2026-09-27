import os
import json
import time
from collections import deque
from flask import Flask, request, jsonify

app = Flask(__name__)

WEBHOOK_TOKEN = os.getenv('WEBHOOK_TOKEN', 'change-me')
PAPER_MODE = os.getenv('PAPER_MODE', 'true').lower() == 'true'
START_BALANCE = float(os.getenv('PAPER_START_BALANCE', '100'))
MAX_LOGS = int(os.getenv('PAPER_MAX_LOGS', '500'))

state = {
    'mode': 'PAPER' if PAPER_MODE else 'LIVE_DISABLED',
    'start_balance': START_BALANCE,
    'last_control': None,
    'last_symbol': None,
    'last_session': None,
    'received': 0,
    'events': 0,
    'controls': 0,
    'logs': deque(maxlen=MAX_LOGS),
}


def now_ms():
    return int(time.time() * 1000)


def log(kind, payload):
    row = {
        'ts': now_ms(),
        'kind': kind,
        'payload': payload,
    }
    state['logs'].appendleft(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def token_ok(token):
    return token == WEBHOOK_TOKEN


def summarize_control(data):
    return {
        'schema': data.get('schema'),
        'type': data.get('type'),
        'sessionId': data.get('sessionId'),
        'symbol': data.get('symbol'),
        'mode': data.get('mode'),
        'anchor': data.get('anchor'),
        'levels': data.get('levels'),
        'leverage': data.get('leverage'),
        'openType': data.get('openType'),
        'entryExecution': data.get('entryExecution'),
        'makerPostOnly': data.get('makerPostOnly'),
        'makerChaseTicks': data.get('makerChaseTicks'),
        'makerMaxRequotes': data.get('makerMaxRequotes'),
        'makerRequoteMs': data.get('makerRequoteMs'),
        'allowMarketFallback': data.get('allowMarketFallback'),
        'pnlTrailStartPct': data.get('pnlTrailStartPct'),
        'pnlTakeProfitPct': data.get('pnlTakeProfitPct'),
        'pnlTrailPullbackPct': data.get('pnlTrailPullbackPct'),
        'pnlMinLockPct': data.get('pnlMinLockPct'),
        'pnlLossStopPct': data.get('pnlLossStopPct'),
        'buyTriggersCount': len(data.get('buyTriggers') or []),
        'sellTriggersCount': len(data.get('sellTriggers') or []),
    }


def summarize_event(evt):
    return {
        'schema': evt.get('schema'),
        'symbol': evt.get('symbol'),
        'action': evt.get('action'),
        'side': evt.get('side'),
        'eventType': evt.get('eventType'),
        'signalPrice': evt.get('signalPrice'),
        'fillPrice': evt.get('fillPrice'),
        'averagePrice': evt.get('averagePrice'),
        'openLegs': evt.get('openLegs'),
        'grossPnlUsdt': evt.get('grossPnlUsdt'),
        'grossPnlPct': evt.get('grossPnlPct'),
        'vol': evt.get('vol'),
        'marginUsdt': evt.get('marginUsdt'),
        'level': evt.get('level'),
        'leverage': evt.get('leverage'),
        'openType': evt.get('openType'),
        'reason': evt.get('reason'),
        'eventTime': evt.get('eventTime'),
        'paperResult': 'LOGGED_ONLY_NO_MEXC_ORDER',
    }


@app.get('/')
def root():
    return jsonify({
        'ok': True,
        'service': 'Sniper FX Paper Bridge',
        'mode': state['mode'],
        'paper_mode': PAPER_MODE,
        'message': 'TradingView webhook test bridge. No MEXC order is sent in PAPER_MODE=true.',
    })


@app.get('/health')
def health():
    return jsonify({
        'ok': True,
        'mode': state['mode'],
        'received': state['received'],
        'controls': state['controls'],
        'events': state['events'],
        'last_symbol': state['last_symbol'],
        'last_session': state['last_session'],
    })


@app.get('/state/<token>')
def get_state(token):
    if not token_ok(token):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    return jsonify({
        'ok': True,
        'mode': state['mode'],
        'start_balance': state['start_balance'],
        'received': state['received'],
        'controls': state['controls'],
        'events': state['events'],
        'last_symbol': state['last_symbol'],
        'last_session': state['last_session'],
        'last_control': state['last_control'],
        'recent_logs': list(state['logs'])[:100],
    })


@app.post('/webhook/<token>')
def webhook(token):
    if not token_ok(token):
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    if not request.is_json:
        return jsonify({'ok': False, 'error': 'JSON body required'}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'ok': False, 'error': 'invalid JSON object'}), 400

    state['received'] += 1
    schema = data.get('schema', '')

    if schema in ('sniper-video-ladder-control-v1', 'sniper-video-ladder-control-v2'):
        summary = summarize_control(data)
        state['controls'] += 1
        state['last_control'] = summary
        state['last_symbol'] = data.get('symbol')
        state['last_session'] = data.get('sessionId')
        log('CONTROL', summary)
        return jsonify({
            'ok': True,
            'paper': True,
            'accepted': 'control',
            'symbol': data.get('symbol'),
            'sessionId': data.get('sessionId'),
            'buyTriggers': len(data.get('buyTriggers') or []),
            'sellTriggers': len(data.get('sellTriggers') or []),
            'mexcOrderSent': False,
        })

    if schema == 'sniper-video-ladder-event-v1':
        summary = summarize_event(data)
        state['events'] += 1
        state['last_symbol'] = data.get('symbol')
        log('EVENT', summary)
        return jsonify({'ok': True, 'paper': True, 'accepted': 'event', 'event': summary, 'mexcOrderSent': False})

    if schema == 'sniper-video-ladder-batch-v1':
        events = data.get('events') or []
        accepted = []
        for evt in events:
            if isinstance(evt, dict):
                summary = summarize_event(evt)
                accepted.append(summary)
                state['events'] += 1
                log('EVENT', summary)
        state['last_session'] = data.get('sessionId')
        return jsonify({
            'ok': True,
            'paper': True,
            'accepted': 'batch',
            'count': len(accepted),
            'events': accepted,
            'mexcOrderSent': False,
        })

    log('UNKNOWN', data)
    return jsonify({'ok': False, 'error': 'unsupported schema', 'schema': schema}), 400


if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    app.run(host='0.0.0.0', port=port)
