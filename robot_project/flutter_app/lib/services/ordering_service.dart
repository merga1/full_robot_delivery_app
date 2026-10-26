import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/ordering_models.dart';
import 'robot_service.dart';

/// Client for the unified restaurant backend (`/api/v1`).
///
/// The robot control API remains on the Raspberry Pi, while ordering, kitchen,
/// admin, and mission workflow calls use this service.  The two tokens are
/// intentionally kept separate.
class OrderingService {
  final String baseUrl;
  final RobotService? robotService;
  final http.Client _client = http.Client();
  String? _accessToken;
  String? _customerToken;
  String? _customerTableCode;

  OrderingService({required String baseUrl, this.robotService})
      : baseUrl = _normalise(baseUrl);

  static String _normalise(String value) {
    var result = value.trim().replaceFirst(RegExp(r'/+$'), '');
    if (!result.endsWith('/api/v1')) result += '/api/v1';
    return result;
  }

  static const _timeout = Duration(seconds: 12);

  Map<String, String> _headers({String? token, bool json = false}) => {
        if (json) 'Content-Type': 'application/json',
        if ((token ?? '').isNotEmpty) 'Authorization': 'Bearer $token',
      };

  String? get _staffToken => _accessToken ?? robotService?.authToken;
  String? get customerTableCode => _customerTableCode;

  Future<dynamic> _request(String method, String path,
      {Object? body, String? token, Map<String, String>? headers}) async {
    final uri = Uri.parse('$baseUrl$path');
    final encoded = body == null ? null : jsonEncode(body);
    late http.Response response;
    try {
      final requestHeaders = {..._headers(token: token, json: body != null), ...?headers};
      response = switch (method) {
        'GET' => await _client.get(uri, headers: requestHeaders).timeout(_timeout),
        'DELETE' => await _client.delete(uri, headers: requestHeaders).timeout(_timeout),
        'PATCH' => await _client.patch(uri, headers: requestHeaders, body: encoded).timeout(_timeout),
        _ => await _client.post(uri, headers: requestHeaders, body: encoded).timeout(_timeout),
      };
    } on TimeoutException {
      throw Exception('The restaurant backend did not answer in time');
    } catch (e) {
      throw Exception('Cannot reach the restaurant backend: $e');
    }
    dynamic data;
    try { data = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body); }
    on FormatException { data = {'detail': response.body.trim()}; }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final detail = data is Map ? data['detail'] : null;
      throw Exception(detail is String ? detail : 'Restaurant API returned HTTP ${response.statusCode}');
    }
    return data;
  }

  Future<void> login(String username, String password) async {
    final data = Map<String, dynamic>.from(await _request('POST', '/auth/login', body: {'username': username, 'password': password}));
    _accessToken = data['access_token']?.toString();
    if ((_accessToken ?? '').isEmpty) throw Exception('Restaurant login did not return an access token');
  }

  void clearCustomerSession() { _customerToken = null; _customerTableCode = null; }

  Future<String> startCustomerSession(String tableCode) async {
    final table = await _request('GET', '/tables/by-code/${Uri.encodeComponent(tableCode)}');
    _customerTableCode = (table['public_code'] ?? tableCode).toString();
    _customerToken = null;
    return _customerTableCode!;
  }

  Future<List<DiningTable>> getPublicTables() async {
    final list = (await _request('GET', '/tables')) as List;
    return list.whereType<Map>().map((e) => DiningTable.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<List<MenuItem>> getCustomerMenu() async {
    final list = (await _request('GET', '/menu')) as List;
    return list.whereType<Map>().map((e) => MenuItem.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  void _rememberOrder(CustomerOrder order) {
    if (order.trackingToken != null && order.trackingToken!.isNotEmpty) _customerToken = order.trackingToken;
  }

  Future<CustomerOrder> placeOrder(List<OrderLine> items, {String note = ''}) async {
    final code = _customerTableCode;
    if (code == null) throw Exception('Choose a table first');
    final data = await _request('POST', '/orders', body: {'table_code': code, 'items': items.map((i) => {'menu_id': i.menuId, 'qty': i.qty}).toList(), if (note.isNotEmpty) 'note': note});
    final order = CustomerOrder.fromJson(Map<String, dynamic>.from(data)); _rememberOrder(order); return order;
  }

  Future<CustomerOrder> callRobot({String note = ''}) async {
    final code = _customerTableCode;
    if (code == null) throw Exception('Choose a table first');
    final data = await _request('POST', '/orders', body: {'table_code': code, 'kind': 'call', if (note.isNotEmpty) 'note': note});
    final order = CustomerOrder.fromJson(Map<String, dynamic>.from(data)); _rememberOrder(order); return order;
  }

  Future<CustomerOrder> trackOrder(String code) async {
    final token = _customerToken;
    if (token == null || token.isEmpty) throw Exception('Place an order first so tracking access can be created');
    final data = await _request('GET', '/orders/track/${Uri.encodeComponent(code)}?token=${Uri.encodeQueryComponent(token)}');
    final order = CustomerOrder.fromJson(Map<String, dynamic>.from(data)); _rememberOrder(order); return order;
  }

  String _requireStaffToken() {
    final token = _staffToken;
    if (token == null || token.isEmpty) throw Exception('Sign in to the restaurant backend first');
    return token;
  }

  Future<List<CustomerOrder>> getOrders({String? status, int limit = 100}) async {
    final query = <String>['limit=$limit']; if (status != null && status != 'open') query.add('status=${Uri.encodeQueryComponent(status)}');
    final data = await _request('GET', '/orders?${query.join('&')}', token: _requireStaffToken());
    final list = (data as List); return list.whereType<Map>().map((e) => CustomerOrder.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<CustomerOrder> _action(int id, String action, {Map<String, dynamic>? body}) async {
    final data = Map<String, dynamic>.from(await _request('POST', '/orders/$id/$action', token: _requireStaffToken(), body: body ?? const {}));
    if (data.containsKey('order')) return CustomerOrder.fromJson(Map<String, dynamic>.from(data['order']));
    final refreshed = await _request('GET', '/orders/$id', token: _requireStaffToken());
    return CustomerOrder.fromJson(Map<String, dynamic>.from(refreshed));
  }

  Future<CustomerOrder> advanceOrder(int orderId) async {
    final orders = await getOrders(limit: 500); final order = orders.firstWhere((item) => item.id == orderId);
    final action = switch (order.status) { 'PENDING' => 'accept', 'ACCEPTED' => 'start-preparation', 'PREPARING' => 'ready', 'READY_FOR_PICKUP' => 'ready-for-dispatch', _ => throw Exception('No kitchen action is available for ${order.status}') };
    return _action(orderId, action);
  }

  Future<CustomerOrder> dispatchOrder(int orderId) async {
    final token = _requireStaffToken();
    final robots = (await _request('GET', '/robots', token: token)) as List;
    final candidate = robots.whereType<Map>().cast<Map<String, dynamic>>().firstWhere(
      (robot) => robot['connection_status'] == 'ONLINE' && robot['availability'] == 'AVAILABLE' && robot['fault_code'] == null,
      orElse: () => <String, dynamic>{},
    );
    if (candidate.isEmpty) throw Exception('No online, available robot is registered');
    await _request('POST', '/orders/$orderId/assign-robot', token: token, body: {'robot_id': candidate['id']});
    await _request('POST', '/orders/$orderId/confirm-loaded', token: token, body: const {});
    await _request('POST', '/orders/$orderId/dispatch', token: token, body: const {});
    final refreshed = await _request('GET', '/orders/$orderId', token: _requireStaffToken());
    return CustomerOrder.fromJson(Map<String, dynamic>.from(refreshed));
  }

  Future<CustomerOrder> cancelOrder(int orderId, {String reason = ''}) => _action(orderId, 'cancel', body: {if (reason.isNotEmpty) 'reason': reason});

  Future<List<MenuItem>> getMenu() async {
    final data = await _request('GET', '/menu?include_unavailable=true', token: _requireStaffToken());
    return (data as List).whereType<Map>().map((e) => MenuItem.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<MenuItem> saveMenuItem(MenuItem item) async { final data = await _request('POST', '/menu', token: _requireStaffToken(), body: item.toJson()); return MenuItem.fromJson(Map<String, dynamic>.from(data)); }
  Future<void> deleteMenuItem(int id) => _request('DELETE', '/menu/$id', token: _requireStaffToken()).then((_) {});

  Future<List<DiningTable>> getTables() async { final list = await _request('GET', '/tables?include_inactive=true', token: _requireStaffToken()); return (list as List).whereType<Map>().map((e) => DiningTable.fromJson(Map<String, dynamic>.from(e))).toList(); }
  Future<DiningTable> saveTable(DiningTable table) async {
    final body = {'table_number': table.code.isEmpty ? table.name : table.code, 'display_name': table.name, 'destination_id': table.destination.isEmpty ? null : table.destination, 'seats': table.seats, 'is_active': table.active};
    final data = await _request('POST', '/tables', token: _requireStaffToken(), body: body); return DiningTable.fromJson(Map<String, dynamic>.from(data));
  }
  Future<void> deleteTable(String code) async { throw Exception('Delete tables by id from the admin web dashboard'); }

  String get _webOrigin => baseUrl.replaceFirst(RegExp(r'/api/v1$'), '');
  String orderPageUrl(String tableCode) => '$_webOrigin/customer/?table=${Uri.encodeQueryComponent(tableCode)}';
  String qrImageUrl(String tableCode) => '$baseUrl/tables/qr/${Uri.encodeComponent(tableCode)}.png';
  String get kitchenUrl => '$_webOrigin/kitchen/';
  void dispose() => _client.close();
}
