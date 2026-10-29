"""Optional, self-contained API tester routes."""
from flask import Blueprint, jsonify, render_template, request
import api_tools

api_bp = Blueprint('api_tools', __name__)


@api_bp.get('/api-tools')
def page():
    return render_template('api_tools.html')


@api_bp.get('/api-tools/status')
def status():
    return jsonify(api_tools.status())


@api_bp.route('/api-tools/setups', methods=['GET', 'POST'])
def setups():
    try:
        if request.method == 'GET':
            return jsonify(setups=api_tools.saved_setups())
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object.')
        return jsonify(success=True, setups=api_tools.saved_setups(data))
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400


@api_bp.post('/api-tools/<action>')
def action(action):
    actions = {'http': api_tools.start_http, 'connect': api_tools.start_ws,
               'send': api_tools.send_ws, 'disconnect': lambda _: api_tools.stop_ws(),
               'clear': lambda data: api_tools.clear(data.get('mode'))}
    if action not in actions:
        return jsonify(success=False, message='Unknown action.'), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, message='Expected a JSON object.'), 400
    try:
        actions[action](data)
        return jsonify(success=True)
    except (ValueError, TypeError, OverflowError):
        return jsonify(success=False, message='Invalid settings or action. Check the URL, headers, timeout and connection state.'), 400


@api_bp.post('/api-tools/export')
def export():
    from export_utils import save_export, SaveCancelled
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('text'), str) or len(data['text']) > 16000000:
        return jsonify(success=False, message='Invalid export.'), 400
    try:
        save_export('api-results.txt', data['text'])
        return jsonify(success=True)
    except SaveCancelled:
        return jsonify(success=True, cancelled=True)
    except OSError:
        return jsonify(success=False, message="Could not save export."), 500
