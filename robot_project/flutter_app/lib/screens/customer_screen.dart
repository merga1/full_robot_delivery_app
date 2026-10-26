import 'dart:async';

import 'package:flutter/material.dart';

import '../models/ordering_models.dart';
import '../services/ordering_service.dart';
import '../theme/app_theme.dart';
import '../widgets/action_button.dart';

/// The customer part of the app: pick your table, order food or call the
/// robot, and follow the order.
///
/// The table is the identity — the QR code on the table carries the table
/// code, the server gives us a token that only works for that table.
class CustomerScreen extends StatefulWidget {
  final OrderingService ordering;
  const CustomerScreen({super.key, required this.ordering});

  @override
  State<CustomerScreen> createState() => _CustomerScreenState();
}

class _CustomerScreenState extends State<CustomerScreen> {
  List<MenuItem> _menu = [];
  List<DiningTable> _tables = [];
  final Map<int, int> _cart = {};          // menu id → quantity
  Map<int, MenuItem> _menuById = {};

  CustomerOrder? _activeOrder;
  Timer? _pollTimer;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  String _tableCode = '';
  String _tableName = '';

  @override
  void initState() {
    super.initState();
    _boot();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _boot() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // The menu and the table list are public — no session needed yet.
      final results = await Future.wait([
        widget.ordering.getCustomerMenu(),
        widget.ordering.getPublicTables(),
      ]);
      if (!mounted) return;
      setState(() {
        _menu = results[0] as List<MenuItem>;
        _tables = results[1] as List<DiningTable>;
        _menuById = {for (final m in _menu) m.id: m};
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString().replaceFirst('Exception: ', '');
      });
    }
  }

  // ── Table selection ────────────────────────────────────────────────────────

  Future<void> _pickTable() async {
    final code = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => _TablePickerSheet(tables: _tables),
    );
    if (code == null || code.isEmpty) return;
    await _startSession(code);
  }

  Future<void> _startSession(String code) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.ordering.startCustomerSession(code);
      final table = _tables.where((t) => t.code == code).firstOrNull;
      if (!mounted) return;
      setState(() {
        _tableCode = code;
        _tableName = table?.name ?? code;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.toString().replaceFirst('Exception: ', '');
      });
    }
  }

  bool get _hasSession => widget.ordering.customerTableCode != null;

  // ── Cart ───────────────────────────────────────────────────────────────────

  int get _cartCount => _cart.values.fold(0, (a, b) => a + b);

  double get _cartTotal {
    var total = 0.0;
    _cart.forEach((id, qty) {
      final item = _menuById[id];
      if (item != null) total += item.price * qty;
    });
    return total;
  }

  void _add(MenuItem item) {
    setState(() {
      _cart[item.id] = (_cart[item.id] ?? 0) + 1;
    });
  }

  void _remove(MenuItem item) {
    setState(() {
      final qty = (_cart[item.id] ?? 0) - 1;
      if (qty <= 0) {
        _cart.remove(item.id);
      } else {
        _cart[item.id] = qty;
      }
    });
  }

  // ── Orders ─────────────────────────────────────────────────────────────────

  Future<void> _placeOrder() async {
    if (_cartCount == 0) {
      _toast('Your order is empty');
      return;
    }
    setState(() => _busy = true);
    try {
      final lines = _cart.entries
          .map((e) => OrderLine(
                menuId: e.key,
                name: _menuById[e.key]?.name ?? '',
                price: _menuById[e.key]?.price ?? 0,
                qty: e.value,
              ))
          .toList();
      final order = await widget.ordering.placeOrder(lines);
      if (!mounted) return;
      setState(() {
        _busy = false;
        _cart.clear();
        _activeOrder = order;
      });
      _startPolling();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      _toast(e.toString().replaceFirst('Exception: ', ''));
    }
  }

  Future<void> _callRobot() async {
    setState(() => _busy = true);
    try {
      final order = await widget.ordering.callRobot();
      if (!mounted) return;
      setState(() {
        _busy = false;
        _activeOrder = order;
      });
      _startPolling();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      _toast(e.toString().replaceFirst('Exception: ', ''));
    }
  }

  void _startPolling() {
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(const Duration(seconds: 3), (_) => _poll());
  }

  Future<void> _poll() async {
    final order = _activeOrder;
    if (order == null) return;
    try {
      final fresh = await widget.ordering.trackOrder(order.code);
      if (!mounted) return;
      setState(() => _activeOrder = fresh);
      if (!fresh.isOpen) _pollTimer?.cancel();
    } catch (_) {
      // offline — the next tick tries again
    }
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_hasSession ? 'Table $_tableName' : 'Order'),
        actions: [
          if (_hasSession)
            IconButton(
              icon: const Icon(Icons.meeting_room_outlined),
              tooltip: 'Change table',
              onPressed: _busy ? null : _pickTable,
            ),
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _loading ? null : _boot,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : !_hasSession
              ? _buildTableGate()
              : _buildOrdering(),
    );
  }

  Widget _buildTableGate() {
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const Card(
          child: Padding(
            padding: EdgeInsets.all(20),
            child: Text(
              'Scan the QR code on your table, or pick the table below.\n'
              'The table tells the kitchen where to bring your order.',
              style: TextStyle(fontSize: 14),
            ),
          ),
        ),
        if (_error != null) ...[
          const SizedBox(height: 12),
          _ErrorCard(message: _error!, onRetry: _boot),
        ],
        const SizedBox(height: 12),
        Text('Tables', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        if (_tables.isEmpty)
          const Padding(
            padding: EdgeInsets.all(12),
            child: Text('No tables are in service right now.'),
          ),
        ..._tables.map((t) => Card(
              child: ListTile(
                leading: const Icon(Icons.table_restaurant, color: AppColors.navy),
                title: Text(t.name),
                subtitle: Text('Code ${t.code}'),
                trailing: const Icon(Icons.chevron_right),
                onTap: _busy ? null : () => _startSession(t.code),
              ),
            )),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: _busy ? null : () => _askForCode(),
          icon: const Icon(Icons.keyboard),
          label: const Text('Enter the table code'),
        ),
      ],
    );
  }

  Future<void> _askForCode() async {
    final controller = TextEditingController(text: _tableCode);
    final code = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Table code'),
        content: TextField(
          controller: controller,
          textCapitalization: TextCapitalization.characters,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'e.g. K7M2Q'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim().toUpperCase()),
            child: const Text('Continue'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (code == null || code.isEmpty) return;
    await _startSession(code);
  }

  Widget _buildOrdering() {
    final order = _activeOrder;
    return Stack(
      children: [
        ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_error != null) ...[
              _ErrorCard(message: _error!, onRetry: () {
                setState(() => _error = null);
              }),
              const SizedBox(height: 12),
            ],
            if (order != null) ...[
              _OrderStatusCard(order: order),
              const SizedBox(height: 12),
            ],
            Text('Menu', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (_menu.isEmpty)
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('The kitchen has not published a menu yet.'),
                ),
              ),
            ..._menu.map(_buildMenuItem),
            const SizedBox(height: 12),
            _buildCart(),
          ],
        ),
        if (_busy)
          const Positioned.fill(
            child: ColoredBox(
              color: Color(0x33000000),
              child: Center(child: CircularProgressIndicator()),
            ),
          ),
      ],
    );
  }

  Widget _buildMenuItem(MenuItem item) {
    final qty = _cart[item.id] ?? 0;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(item.name,
                      style: const TextStyle(
                          fontWeight: FontWeight.w700, fontSize: 15)),
                  if (item.description.isNotEmpty)
                    Text(item.description,
                        style: const TextStyle(
                            fontSize: 12, color: AppColors.textSecondary)),
                  Text('\$${item.price.toStringAsFixed(2)}',
                      style: const TextStyle(
                          color: AppColors.green, fontWeight: FontWeight.w700)),
                ],
              ),
            ),
            if (qty > 0) ...[
              IconButton(
                icon: const Icon(Icons.remove_circle_outline),
                onPressed: () => _remove(item),
              ),
              Text('$qty',
                  style: const TextStyle(
                      fontWeight: FontWeight.w700, fontSize: 16)),
              const SizedBox(width: 8),
            ],
            IconButton.filled(
              icon: const Icon(Icons.add),
              onPressed: () => _add(item),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildCart() {
    if (_cartCount == 0) return const SizedBox.shrink();
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('$_cartCount item${_cartCount == 1 ? '' : 's'}',
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                Text('\$${_cartTotal.toStringAsFixed(2)}',
                    style: const TextStyle(
                        fontWeight: FontWeight.w700, fontSize: 16)),
              ],
            ),
            const SizedBox(height: 12),
            ActionButton(
              label: 'SEND TO THE KITCHEN',
              icon: Icons.restaurant_menu,
              color: AppColors.green,
              loading: _busy,
              onPressed: _placeOrder,
            ),
            const SizedBox(height: 8),
            ActionButton(
              label: 'CALL THE ROBOT',
              icon: Icons.smart_toy_outlined,
              color: AppColors.orange,
              loading: _busy,
              onPressed: _callRobot,
            ),
          ],
        ),
      ),
    );
  }
}

/// The little card that shows where the order stands.
class _OrderStatusCard extends StatelessWidget {
  final CustomerOrder order;
  const _OrderStatusCard({required this.order});

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(order.status);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(order.isCall ? Icons.smart_toy : Icons.receipt_long,
                    color: color),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    order.isCall ? 'Robot request' : 'Order ${order.code}',
                    style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(order.status,
                      style: TextStyle(
                          color: color, fontWeight: FontWeight.w700, fontSize: 12)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(_statusText(order),
                style: const TextStyle(
                    fontSize: 13, color: AppColors.textSecondary)),
            if (order.error != null && order.error!.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(order.error!,
                  style: const TextStyle(fontSize: 13, color: AppColors.red)),
            ],
          ],
        ),
      ),
    );
  }

  static String _statusText(CustomerOrder o) {
    switch (o.status) {
      case 'placed':
        return o.isCall
            ? 'The kitchen has your request — the robot is coming.'
            : 'The kitchen has received your order.';
      case 'accepted':
        return 'The kitchen accepted your order.';
      case 'preparing':
        return 'Your food is being prepared.';
      case 'ready':
        return 'Ready — the robot is on its way.';
      case 'delivering':
        return 'The robot is on its way to your table.';
      case 'delivered':
        return 'Enjoy your order!';
      case 'cancelled':
        return 'This request was cancelled.';
      default:
        return '';
    }
  }

  static Color _statusColor(String status) {
    switch (status) {
      case 'placed':
        return AppColors.orange;
      case 'accepted':
        return AppColors.navy;
      case 'preparing':
        return AppColors.orange;
      case 'ready':
        return AppColors.green;
      case 'delivering':
        return AppColors.navy;
      case 'delivered':
        return AppColors.green;
      case 'cancelled':
        return AppColors.red;
      default:
        return AppColors.textSecondary;
    }
  }
}

/// Bottom sheet with the list of tables + a field for the code.
class _TablePickerSheet extends StatefulWidget {
  final List<DiningTable> tables;
  const _TablePickerSheet({required this.tables});

  @override
  State<_TablePickerSheet> createState() => _TablePickerSheetState();
}

class _TablePickerSheetState extends State<_TablePickerSheet> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding:
          EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Your table',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              const SizedBox(height: 12),
              TextField(
                controller: _controller,
                textCapitalization: TextCapitalization.characters,
                decoration: InputDecoration(
                  labelText: 'Table code',
                  hintText: 'e.g. K7M2Q',
                  suffixIcon: IconButton(
                    icon: const Icon(Icons.arrow_forward),
                    onPressed: () => Navigator.pop(
                        context, _controller.text.trim().toUpperCase()),
                  ),
                ),
                onSubmitted: (v) =>
                    Navigator.pop(context, v.trim().toUpperCase()),
              ),
              const SizedBox(height: 12),
              ...widget.tables.map((t) => ListTile(
                    leading: const Icon(Icons.table_restaurant, color: AppColors.navy),
                    title: Text(t.name),
                    subtitle: Text('Code ${t.code}'),
                    onTap: () => Navigator.pop(context, t.code),
                  )),
            ],
          ),
        ),
      ),
    );
  }
}

class _ErrorCard extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  const _ErrorCard({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.red.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: AppColors.red.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, color: AppColors.red, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(message,
                style: const TextStyle(color: AppColors.red, fontSize: 13)),
          ),
          IconButton(
            icon: const Icon(Icons.close, size: 16, color: AppColors.red),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(),
            onPressed: onRetry,
          ),
        ],
      ),
    );
  }
}
