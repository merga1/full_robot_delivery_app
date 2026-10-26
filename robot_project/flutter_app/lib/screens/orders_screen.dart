import 'dart:async';

import 'package:flutter/material.dart';

import '../models/ordering_models.dart';
import '../services/ordering_service.dart';
import '../theme/app_theme.dart';
import '../widgets/action_button.dart';

/// The order board — the same thing the kitchen sees on the web dashboard,
/// but on the phone.  The operator/admin uses it to follow the orders and to
/// send the robot; the kitchen uses it to move the orders along.
class OrdersScreen extends StatefulWidget {
  final OrderingService ordering;
  final bool canChange;
  const OrdersScreen({super.key, required this.ordering, this.canChange = true});

  @override
  State<OrdersScreen> createState() => _OrdersScreenState();
}

class _OrdersScreenState extends State<OrdersScreen> {
  List<CustomerOrder> _orders = [];
  bool _loading = true;
  bool _busy = false;
  bool _showClosed = false;
  String? _error;
  Timer? _pollTimer;

  @override
  void initState() {
    super.initState();
    _load();
    _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) => _load());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final orders = await widget.ordering.getOrders(
        status: _showClosed ? null : 'open',
      );
      if (!mounted) return;
      setState(() {
        _orders = orders;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.toString().replaceFirst('Exception: ', '');
      });
    }
  }

  Future<void> _act(CustomerOrder order, String action) async {
    setState(() => _busy = true);
    try {
      switch (action) {
        case 'advance':
          await widget.ordering.advanceOrder(order.id);
          break;
        case 'dispatch':
          if (order.status == 'READY_FOR_PICKUP') {
            await widget.ordering.advanceOrder(order.id);
          } else {
            await widget.ordering.dispatchOrder(order.id);
          }
          break;
        case 'cancel':
          await widget.ordering.cancelOrder(order.id);
          break;
      }
      if (!mounted) return;
      setState(() => _busy = false);
      await _load();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
      await _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Orders'),
        actions: [
          IconButton(
            icon: Icon(_showClosed ? Icons.filter_alt : Icons.filter_alt_outlined),
            tooltip: _showClosed ? 'Only open orders' : 'Show all orders',
            onPressed: () {
              setState(() => _showClosed = !_showClosed);
              _load();
            },
          ),
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _load,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                if (_error != null)
                  Container(
                    width: double.infinity,
                    margin: const EdgeInsets.all(12),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: AppColors.red.withValues(alpha: 0.08),
                      borderRadius: BorderRadius.circular(10),
                      border:
                          Border.all(color: AppColors.red.withValues(alpha: 0.3)),
                    ),
                    child: Text(_error!,
                        style: const TextStyle(color: AppColors.red, fontSize: 13)),
                  ),
                Expanded(
                  child: _orders.isEmpty
                      ? const Center(
                          child: Text('No open orders',
                              style: TextStyle(color: AppColors.textSecondary)),
                        )
                      : RefreshIndicator(
                          onRefresh: _load,
                          child: ListView.builder(
                            padding: const EdgeInsets.all(12),
                            itemCount: _orders.length,
                            itemBuilder: (_, i) =>
                                _buildOrderCard(_orders[i]),
                          ),
                        ),
                ),
              ],
            ),
    );
  }

  Widget _buildOrderCard(CustomerOrder order) {
    final status = OrderStatus.parse(order.status);
    final color = _statusColor(order.status);
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(order.isCall ? Icons.smart_toy : Icons.receipt_long,
                    color: color, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    order.isCall ? 'Call the robot' : 'Order ${order.code}',
                    style: const TextStyle(
                        fontWeight: FontWeight.w700, fontSize: 15),
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
                          color: color,
                          fontWeight: FontWeight.w700,
                          fontSize: 12)),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text('Table ${order.tableCode}',
                style: const TextStyle(
                    fontSize: 13, color: AppColors.textSecondary)),
            if (order.items.isNotEmpty) ...[
              const SizedBox(height: 6),
              ...order.items.map((i) => Padding(
                    padding: const EdgeInsets.only(bottom: 2),
                    child: Text('${i.qty} x ${i.name}  ·  '
                        '\$${(i.price * i.qty).toStringAsFixed(2)}',
                        style: const TextStyle(fontSize: 13)),
                  )),
              const Divider(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text('Total',
                      style: TextStyle(
                          fontSize: 13, color: AppColors.textSecondary)),
                  Text('\$${order.total.toStringAsFixed(2)}',
                      style: const TextStyle(
                          fontWeight: FontWeight.w700, fontSize: 15)),
                ],
              ),
            ],
            if (order.note.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text('Note: ${order.note}',
                  style: const TextStyle(
                      fontSize: 13, color: AppColors.textSecondary)),
            ],
            if (order.error != null && order.error!.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(order.error!,
                  style: const TextStyle(fontSize: 13, color: AppColors.red)),
            ],
            if (widget.canChange && order.isOpen) ...[
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: ActionButton(
                      label: status.actionLabel.toUpperCase(),
                      icon: status == OrderStatus.ready
                          ? Icons.smart_toy
                          : Icons.arrow_forward,
                      color: status == OrderStatus.ready
                          ? AppColors.green
                          : AppColors.navy,
                      loading: _busy,
                      onPressed: () => _act(
                          order,
                          status == OrderStatus.ready
                              ? 'dispatch'
                              : 'advance'),
                    ),
                  ),
                  const SizedBox(width: 10),
                  OutlinedButton(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: AppColors.red,
                      minimumSize: const Size(48, 48),
                    ),
                    onPressed: _busy ? null : () => _act(order, 'cancel'),
                    child: const Icon(Icons.close),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
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
