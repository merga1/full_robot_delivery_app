/// Models for the ordering side of the robot: the menu, the dining tables and
/// the customer orders (food orders and "call the robot" requests).
library;

/// One thing the kitchen can sell.
class MenuItem {
  final int id;
  final String name;
  final String description;
  final double price;
  final String category;
  final bool available;

  const MenuItem({
    required this.id,
    required this.name,
    this.description = '',
    required this.price,
    this.category = 'General',
    this.available = true,
  });

  factory MenuItem.fromJson(Map<String, dynamic> json) => MenuItem(
        id: _asInt(json['id']),
        name: (json['name'] ?? '').toString(),
        description: (json['description'] ?? '').toString(),
        price: _asDouble(json['price']),
        category: (json['category'] ?? 'General').toString(),
        available: json['available'] != false,
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'description': description,
        'price': price,
        'category': category,
        'available': available,
      };
}

/// A table in the restaurant.  [code] is what the QR code on the table carries.
class DiningTable {
  final String code;
  final String name;
  final String destination;
  final int seats;
  final bool active;

  const DiningTable({
    required this.code,
    required this.name,
    this.destination = '',
    this.seats = 4,
    this.active = true,
  });

  factory DiningTable.fromJson(Map<String, dynamic> json) => DiningTable(
        code: (json['code'] ?? json['public_code'] ?? json['table_number'] ?? '').toString(),
        name: (json['name'] ?? json['display_name'] ?? '').toString(),
        destination: (json['destination'] ?? json['destination_id'] ?? '').toString(),
        seats: _asInt(json['seats'], fallback: 4),
        active: json['active'] ?? json['is_active'] ?? true,
      );

  Map<String, dynamic> toJson() => {
        'code': code,
        'name': name,
        'destination': destination,
        'seats': seats,
        'active': active,
      };
}

/// One line of an order.  The price is the one the server calculated.
class OrderLine {
  final int menuId;
  final String name;
  final double price;
  final int qty;

  const OrderLine({
    required this.menuId,
    required this.name,
    required this.price,
    required this.qty,
  });

  factory OrderLine.fromJson(Map<String, dynamic> json) => OrderLine(
        menuId: _asInt(json['menu_id'] ?? json['menu_item_id']),
        name: (json['name'] ?? '').toString(),
        price: _asDouble(json['price']),
        qty: _asInt(json['qty'], fallback: 1),
      );

  double get total => price * qty;
}

/// A customer order — either food ([kind] == 'order') or a robot call
/// ([kind] == 'call').
class CustomerOrder {
  final int id;
  final String code;
  final String kind;
  final String status;
  final String tableCode;
  final List<OrderLine> items;
  final double total;
  final String note;
  final String? error;
  final int? missionId;
  final String? createdAt;
  final String? trackingToken;

  const CustomerOrder({
    required this.id,
    required this.code,
    required this.kind,
    required this.status,
    this.tableCode = '',
    this.items = const [],
    this.total = 0,
    this.note = '',
    this.error,
    this.missionId,
    this.createdAt,
    this.trackingToken,
  });

  bool get isCall => kind == 'call';
  bool get isOpen => !const {'DELIVERED', 'CANCELLED', 'REJECTED'}.contains(status.toUpperCase());

  factory CustomerOrder.fromJson(Map<String, dynamic> json) => CustomerOrder(
        id: _asInt(json['id']),
        code: (json['code'] ?? '').toString(),
        kind: (json['kind'] ?? 'order').toString(),
        status: (json['status'] ?? 'PENDING').toString(),
        tableCode: (json['table_code'] ?? json['table_label'] ?? '').toString(),
        items: ((json['items'] ?? const []) as List)
            .whereType<Map>()
            .map((e) => OrderLine.fromJson(Map<String, dynamic>.from(e)))
            .toList(),
        total: _asDouble(json['total']),
        note: (json['note'] ?? '').toString(),
        error: json['error']?.toString(),
        missionId: json['mission_id'] == null ? null : _asInt(json['mission_id']),
        createdAt: json['created_at']?.toString(),
        trackingToken: json['tracking_token']?.toString(),
      );
}

/// The status machine of an order — the kitchen walks through it.
enum OrderStatus {
  placed,
  accepted,
  preparing,
  ready,
  delivering,
  delivered,
  cancelled;

  static OrderStatus parse(String? raw) {
    final normalised = (raw ?? '').toUpperCase();
    if (normalised == 'PENDING' || normalised == 'PLACED') return OrderStatus.placed;
    if (normalised == 'ACCEPTED') return OrderStatus.accepted;
    if (normalised == 'PREPARING') return OrderStatus.preparing;
    if (normalised == 'READY_FOR_PICKUP' || normalised == 'READY_FOR_DISPATCH' || normalised == 'LOADED') return OrderStatus.ready;
    if (normalised == 'OUT_FOR_DELIVERY' || normalised == 'ARRIVED_AT_TABLE') return OrderStatus.delivering;
    if (normalised == 'COLLECTION_CONFIRMED' || normalised == 'DELIVERED') return OrderStatus.delivered;
    if (normalised == 'REJECTED' || normalised == 'CANCELLED' || normalised == 'DELIVERY_FAILED') return OrderStatus.cancelled;
    for (final s in OrderStatus.values) {
      if (s.name == raw) return s;
    }
    return OrderStatus.placed;
  }

  /// The status the kitchen button moves the order to.
  OrderStatus? get next {
    switch (this) {
      case OrderStatus.placed:
        return OrderStatus.accepted;
      case OrderStatus.accepted:
        return OrderStatus.preparing;
      case OrderStatus.preparing:
        return OrderStatus.ready;
      case OrderStatus.ready:
        return OrderStatus.delivering;
      case OrderStatus.delivering:
        return OrderStatus.delivered;
      case OrderStatus.delivered:
      case OrderStatus.cancelled:
        return null;
    }
  }

  /// The label of the button the kitchen presses for this status.
  String get actionLabel {
    switch (this) {
      case OrderStatus.placed:
        return 'Accept';
      case OrderStatus.accepted:
        return 'Start preparing';
      case OrderStatus.preparing:
        return 'Ready';
      case OrderStatus.ready:
        return 'Send the robot';
      case OrderStatus.delivering:
        return 'Mark delivered';
      case OrderStatus.delivered:
        return 'Delivered';
      case OrderStatus.cancelled:
        return 'Cancelled';
    }
  }
}

int _asInt(dynamic value, {int fallback = 0}) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value) ?? fallback;
  return fallback;
}

double _asDouble(dynamic value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value) ?? 0;
  return 0;
}
