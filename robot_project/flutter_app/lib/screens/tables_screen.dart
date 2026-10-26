import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/ordering_models.dart';
import '../services/ordering_service.dart';
import '../services/robot_service.dart';
import '../theme/app_theme.dart';
import '../widgets/action_button.dart';

/// The tables of the restaurant.  Every table has a code and a QR code that
/// the customer scans to order — this screen creates them, shows the QR code
/// for printing and lets the admin jump to the order page.
class TablesScreen extends StatefulWidget {
  final OrderingService ordering;
  final RobotService? robotService;
  const TablesScreen({super.key, required this.ordering, this.robotService});

  @override
  State<TablesScreen> createState() => _TablesScreenState();
}

class _TablesScreenState extends State<TablesScreen> {
  List<DiningTable> _tables = [];
  List<String> _destinations = [];
  bool _loading = true;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        widget.ordering.getTables(),
        if (widget.robotService != null)
          widget.robotService!.getDestinations()
        else
          Future<List<String>>.value(const []),
      ]);
      if (!mounted) return;
      setState(() {
        _tables = results[0] as List<DiningTable>;
        _destinations = results[1] as List<String>;
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

  Future<void> _save(DiningTable table) async {
    setState(() => _busy = true);
    try {
      await widget.ordering.saveTable(table);
      if (!mounted) return;
      setState(() => _busy = false);
      await _load();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
    }
  }

  Future<void> _delete(DiningTable table) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Delete ${table.name}?'),
        content: const Text(
            'The table disappears from the customer page. Orders that were '
            'already placed are kept.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await widget.ordering.deleteTable(table.code);
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
    }
  }

  Future<void> _edit(DiningTable? existing) async {
    final nameCtrl =
        TextEditingController(text: existing?.name ?? '');
    final destCtrl =
        TextEditingController(text: existing?.destination ?? '');
    final seatsCtrl =
        TextEditingController(text: '${existing?.seats ?? 4}');
    String destination = existing?.destination ?? '';

    final saved = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setInner) => AlertDialog(
          title: Text(existing == null ? 'New table' : 'Edit ${existing.name}'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: nameCtrl,
                  autofocus: true,
                  decoration: const InputDecoration(labelText: 'Name'),
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  initialValue: _destinations.contains(destination)
                      ? destination
                      : (destination.isEmpty ? null : destination),
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'Robot destination',
                    helperText:
                        'Where the robot stops — the destinations of the map',
                  ),
                  items: [
                    const DropdownMenuItem(
                        value: '', child: Text('(none yet)')),
                    ..._destinations.map(
                        (d) => DropdownMenuItem(value: d, child: Text(d))),
                  ],
                  onChanged: (v) => setInner(() => destination = v ?? ''),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: seatsCtrl,
                  keyboardType: TextInputType.number,
                  decoration:
                      const InputDecoration(labelText: 'Seats', hintText: '4'),
                ),
                if (existing != null) ...[
                  const SizedBox(height: 12),
                  Text('Code: ${existing.code}',
                      style: const TextStyle(
                          fontSize: 12, color: AppColors.textSecondary)),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Cancel')),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Save'),
            ),
          ],
        ),
      ),
    );

    final name = nameCtrl.text.trim();
    final seats = int.tryParse(seatsCtrl.text.trim()) ?? 4;
    nameCtrl.dispose();
    destCtrl.dispose();
    seatsCtrl.dispose();
    if (saved != true || name.isEmpty) return;

    await _save(DiningTable(
      code: existing?.code ?? '',
      name: name,
      destination: destination,
      seats: seats,
      active: existing?.active ?? true,
    ));
  }

  void _showQr(DiningTable table) {
    showDialog<void>(
      context: context,
      builder: (ctx) => Dialog(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(table.name,
                  style:
                      const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              Text('Code ${table.code}',
                  style: const TextStyle(
                      fontSize: 12, color: AppColors.textSecondary)),
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(10),
                color: Colors.white,
                child: Image.network(
                  widget.ordering.qrImageUrl(table.code),
                  width: 240,
                  height: 240,
                  fit: BoxFit.contain,
                  errorBuilder: (_, _, _) => const SizedBox(
                    height: 240,
                    child: Center(
                      child: Text('The QR code could not be loaded',
                          textAlign: TextAlign.center),
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Text(
                widget.ordering.orderPageUrl(table.code),
                style: const TextStyle(
                    fontSize: 11, color: AppColors.textSecondary),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 16),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  OutlinedButton.icon(
                    onPressed: () {
                      launchUrl(
                        Uri.parse(widget.ordering.orderPageUrl(table.code)),
                        mode: LaunchMode.externalApplication,
                      );
                    },
                    icon: const Icon(Icons.open_in_new, size: 18),
                    label: const Text('Order page'),
                  ),
                  FilledButton.icon(
                    onPressed: () => Navigator.pop(ctx),
                    icon: const Icon(Icons.check, size: 18),
                    label: const Text('Done'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tables'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh',
            onPressed: _load,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: AppColors.navy,
        foregroundColor: Colors.white,
        icon: const Icon(Icons.add),
        label: const Text('New table'),
        onPressed: _busy ? null : () => _edit(null),
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
                  child: _tables.isEmpty
                      ? const Center(
                          child: Text(
                            'No tables yet — tap "New table" to create the first one.',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: AppColors.textSecondary),
                          ),
                        )
                      : ListView.builder(
                          padding: const EdgeInsets.all(12),
                          itemCount: _tables.length,
                          itemBuilder: (_, i) => _buildTile(_tables[i]),
                        ),
                ),
              ],
            ),
    );
  }

  Widget _buildTile(DiningTable table) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: AppColors.navy.withValues(alpha: 0.12),
          child: Text(
            table.name.characters.first.toUpperCase(),
            style: const TextStyle(
                color: AppColors.navy, fontWeight: FontWeight.w700),
          ),
        ),
        title: Row(
          children: [
            Flexible(child: Text(table.name)),
            const SizedBox(width: 8),
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: AppColors.textSecondary.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(table.code,
                  style: const TextStyle(fontSize: 11, letterSpacing: 1)),
            ),
            if (!table.active) ...[
              const SizedBox(width: 8),
              const Text('OUT OF SERVICE',
                  style: TextStyle(fontSize: 10, color: AppColors.red)),
            ],
          ],
        ),
        subtitle: Text(
          table.destination.isEmpty
              ? 'No robot destination yet · ${table.seats} seats'
              : 'Robot stops at "${table.destination}" · ${table.seats} seats',
          style: const TextStyle(fontSize: 12),
        ),
        trailing: PopupMenuButton<String>(
          onSelected: (v) {
            if (v == 'qr') {
              _showQr(table);
            } else if (v == 'edit') {
              _edit(table);
            } else if (v == 'delete') {
              _delete(table);
            }
          },
          itemBuilder: (_) => const [
            PopupMenuItem(value: 'qr', child: Text('Show QR code')),
            PopupMenuItem(value: 'edit', child: Text('Edit')),
            PopupMenuItem(value: 'delete', child: Text('Delete')),
          ],
        ),
      ),
    );
  }
}
