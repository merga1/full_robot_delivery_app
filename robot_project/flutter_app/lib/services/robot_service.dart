import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/robot_state.dart';
import '../models/map_models.dart';

/// REST client for the Pi. Polling is intentional: it keeps the app useful on
/// lab Wi-Fi where a WebSocket connection is frequently interrupted.
///
/// Performance/safety rules (these fix the lag + "stop arrives late" bugs):
///  * ONE persistent [http.Client] (keep-alive) — the old code opened a new TCP
///    connection for every request (about 16/s while driving).
///  * The status poll never overlaps itself.
///  * Joystick commands never pile up: while one is in flight only the LATEST
///    command is kept, so a "stop" can never wait behind stale "go" commands.
///  * Short timeouts: a dead link is reported in seconds, not after 75 s.
///  * Poll failures are surfaced (state becomes 'disconnected') instead of
///    leaving the UI frozen on stale data.
class RobotService {
  final String baseUrl;
  String? _token;
  String? role;
  Timer? _poller;
  final http.Client _client = http.Client();
  final _states = StreamController<RobotState>.broadcast();
  Stream<RobotState> get stateStream => _states.stream;

  // Fires once when the API rejects the current token (expired/invalid, HTTP
  // 401) so the UI can prompt for re-login instead of the state stream just
  // silently going quiet. JWTs expire server-side (ROBOT_TOKEN_TTL_HOURS,
  // default 12 h) regardless of the client's idle-timeout setting.
  final _authExpired = StreamController<void>.broadcast();
  Stream<void> get authExpiredStream => _authExpired.stream;
  bool _authExpiredFired = false;

  RobotState _lastState = RobotState.initial();
  RobotState get lastState => _lastState;
  bool _polling = false;
  int _pollFailures = 0;
  int _cmdSeq = 0;

  static const _getTimeout = Duration(seconds: 8);
  static const _postTimeout = Duration(seconds: 12);
  static const _cmdTimeout = Duration(seconds: 1);

  RobotService({required this.baseUrl});

  Map<String, String> get _headers => {
    'Content-Type': 'application/json',
    if (_token != null) 'Authorization': 'Bearer $_token',
  };

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Object? body,
    Duration? timeout,
  }) async {
    final uri = Uri.parse('$baseUrl$path');
    final encoded = body == null ? null : jsonEncode(body);
    late http.Response response;
    try {
      response = switch (method) {
        'GET' => await _client.get(uri, headers: _headers).timeout(timeout ?? _getTimeout),
        'DELETE' => await _client.delete(uri, headers: _headers).timeout(timeout ?? _getTimeout),
        _ => await _client.post(uri, headers: _headers, body: encoded).timeout(timeout ?? _postTimeout),
      };
    } on TimeoutException {
      throw Exception('Robot did not answer in time (${(timeout ?? (method == 'GET' ? _getTimeout : _postTimeout)).inSeconds} s)');
    } catch (e) {
      throw Exception('Cannot reach robot API: $e');
    }
    Map<String, dynamic> data = {};
    if (response.body.isNotEmpty) {
      try {
        final value = jsonDecode(response.body);
        if (value is Map<String, dynamic>) data = value;
      } on FormatException {
        // Reverse proxies and an unhealthy Pi backend can return plain text
        // (for example, "Internal Server Error").  Preserve it for the user
        // instead of masking the actual server failure with a JSON exception.
        data = {'detail': response.body.trim()};
      }
    }
    if (response.statusCode == 401 && !_authExpiredFired) {
      _authExpiredFired = true;
      _authExpired.add(null);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final detail = data['detail'];
      throw Exception(detail is String ? detail : (detail?.toString() ?? 'Robot API returned HTTP ${response.statusCode}'));
    }
    return data;
  }

  Future<void> login(String username, String password) async {
    final data = await _request('POST', '/auth/login', body: {'username': username, 'password': password});
    _token = data['access_token']?.toString();
    role = data['role']?.toString();
    _authExpiredFired = false;
  }

  void connect() {
    _poller?.cancel();
    _pollFailures = 0;
    _poller = Timer.periodic(const Duration(seconds: 1), (_) => _poll());
    _poll();
  }

  Future<void> _poll() async {
    if (_polling) return; // never overlap: a slow reply must not stack up requests
    _polling = true;
    try {
      final next = RobotState.fromJson(await _request('GET', '/robot/status'));
      _pollFailures = 0;
      _lastState = next;
      _states.add(next);
    } catch (e) {
      _pollFailures++;
      // Tolerate a single dropped poll, then tell the UI the link is down.
      if (_pollFailures >= 3 && !_states.isClosed) {
        _lastState = RobotState.disconnected(_lastState, 'Connection lost: ${e.toString().replaceFirst('Exception: ', '')}');
        _states.add(_lastState);
      }
    } finally {
      _polling = false;
    }
  }

  void disconnect() { _poller?.cancel(); _poller = null; }
  void logout() { disconnect(); _token = null; role = null; }

  /// Release the persistent connection. Call when the service is discarded.
  void dispose() {
    disconnect();
    _client.close();
    _states.close();
    _authExpired.close();
  }

  Future<void> startRobot() async { await _request('POST', '/robot/start'); }
  Future<void> stopRobot() async { await _request('POST', '/robot/stop'); }
  Future<Map<String, dynamic>> setReady() => _request('POST', '/robot/ready');

  // Pi endpoint is /robot/link-check, not /robot/real1-link
  Future<Map<String, dynamic>> checkReal1Link() => _request('GET', '/robot/link-check');
  Future<Map<String, dynamic>> getBridgeStatus() => _request('GET', '/robot/bridge-status');

  // Pi endpoint is /teleop/enable (not /robot/teleop)
  Future<Map<String, dynamic>> enableTeleop(bool enabled) =>
      _request('POST', '/teleop/enable', body: {'enabled': enabled});

  bool _cmdInFlight = false;
  double? _pendingLinear;
  double? _pendingAngular;

  /// Send a drive command. If one is already in flight, only the newest
  /// command is kept and sent right after it (latest wins) — so a stop is never
  /// queued behind stale commands. Returns the server reply of the last request
  /// (`{'queued': true}` if this call was merged into the in-flight one).
  Future<Map<String, dynamic>> sendCmdVel(double linear, double angular) async {
    if (_cmdInFlight) {
      _pendingLinear = linear;
      _pendingAngular = angular;
      return {'queued': true};
    }
    _cmdInFlight = true;
    Map<String, dynamic> result = {};
    Object? error;
    var lin = linear;
    var ang = angular;
    try {
      while (true) {
        try {
          result = await _request(
            'POST',
            '/teleop/cmd_vel',
            body: {'linear': lin, 'angular': ang, 'seq': ++_cmdSeq},
            timeout: _cmdTimeout,
          );
          error = null;
        } catch (e) {
          error = e;
        }
        final pl = _pendingLinear;
        final pa = _pendingAngular;
        if (pl == null || pa == null) break;
        _pendingLinear = null;
        _pendingAngular = null;
        lin = pl;
        ang = pa;
      }
    } finally {
      _cmdInFlight = false;
    }
    if (error != null) throw error;
    return result;
  }

  Future<Map<String, dynamic>> enableMotors() => _request('POST', '/robot/enable-motors');

  // Pi endpoint is /teleop/test-drive (not /robot/test-drive)
  Future<Map<String, dynamic>> testDrive() => _request('POST', '/teleop/test-drive');

  // Pi endpoint is /teleop/debug (not /robot/teleop-debug)
  Future<Map<String, dynamic>> getTeleopDebug() => _request('GET', '/teleop/debug');

  // Pi endpoint is /teleop/connection (not /robot/teleop-connection)
  Future<Map<String, dynamic>> getTeleopConnection() => _request('GET', '/teleop/connection');

  Future<void> stopTeleop() async { await _request('POST', '/teleop/stop', timeout: _cmdTimeout); }

  Future<void> cancelNavigation() async { await _request('POST', '/navigation/cancel'); }

  /// POST /navigation/goal. The Pi now waits for Nav2 to accept/reject the goal
  /// and answers with an HTTP error + reason if it cannot navigate (Nav2 not
  /// running, robot not localized, goal rejected, mission running …).
  Future<Map<String, dynamic>> sendGoal(String name) =>
      _request('POST', '/navigation/goal', body: {'destination': name});

  /// Detailed state of the current/last navigation goal.
  Future<Map<String, dynamic>> getNavStatus() => _request('GET', '/navigation/status');

  /// Tell AMCL where the robot is (like "2D Pose Estimate" in RViz). Degrees.
  Future<Map<String, dynamic>> setInitialPose(double x, double y, double thetaDeg) =>
      _request('POST', '/navigation/initial-pose', body: {'x': x, 'y': y, 'theta': thetaDeg});

  // Pi returns {"destinations": [{"name":"...","x":...,"y":...,"theta":...}, ...]}
  // Also handles old-format lists of strings for backward compatibility.
  Future<List<String>> getDestinations() async {
    final raw = (await _request('GET', '/destinations'))['destinations'];
    if (raw is List) {
      return raw.map((e) {
        if (e is Map) return (e['name'] ?? '').toString();
        return e.toString();
      }).toList()..sort();
    }
    if (raw is Map) {
      return (raw.keys.map((e) => e.toString()).toList())..sort();
    }
    return [];
  }

  /// Returns destinations with full pose data for the NavigationScreen.
  Future<List<DestinationPose>> getDestinationPoses() async {
    final raw = (await _request('GET', '/destinations'))['destinations'];
    if (raw is List) {
      return raw
          .map((e) {
            if (e is Map<String, dynamic>) {
              return DestinationPose.fromJson(e);
            }
            // Old format: just a name string, no coordinates
            return DestinationPose(
              name: e.toString(),
              x: 0,
              y: 0,
              theta: 0,
            );
          })
          .toList();
    }
    return [];
  }

  /// Full URL for the map image — used by NavigationScreen to load the map.
  String get mapImageUrl => '$baseUrl/navigation/map/image';

  /// Current auth token (needed to add Authorization header to image requests).
  String? get authToken => _token;

  /// Fetch map metadata (resolution, origin, image size).
  Future<MapMeta> getMapMeta() async {
    final data = await _request('GET', '/navigation/map');
    return MapMeta.fromJson(data);
  }

  // Pi endpoint is POST /destinations with body {name, x, y, theta}
  Future<void> addDestination(String name, double x, double y, double theta) async =>
      _request('POST', '/destinations', body: {'name': name, 'x': x, 'y': y, 'theta': theta});

  Future<void> deleteDestination(String name) async =>
      _request('DELETE', '/destinations/${Uri.encodeComponent(name)}');

  Future<Map<String, dynamic>> createMission(List<String> destinations) =>
      _request('POST', '/missions', body: {'destinations': destinations});
  Future<Map<String, dynamic>> getMission(int id) => _request('GET', '/missions/$id');
  Future<Map<String, dynamic>> cancelMission(int id) => _request('POST', '/missions/$id/cancel');

  // Fetch event logs from Pi /logs endpoint
  Future<Map<String, dynamic>> getLogs({String? severity, int limit = 100}) {
    final params = <String>[];
    if (severity != null) params.add('severity=$severity');
    params.add('limit=$limit');
    final query = params.isEmpty ? '' : '?${params.join('&')}';
    return _request('GET', '/logs$query');
  }
}
