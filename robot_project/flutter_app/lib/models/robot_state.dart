/// Live robot status from backend WebSocket / REST
class RobotState {
  final String connection;
  final String status;
  final RobotPose pose;
  final String? currentAction;
  final String? destination;
  final bool rosRunning;
  final String message;
  final Map<String, String> nodeHealth;
  final int? currentMission;
  final Map<String, dynamic> bridgeStatus;
  // Battery fields — populated by /robot/status and /ws/status payloads
  final int battery;
  final double batteryVoltage;
  final String batteryAlert;
  /// False when the Pi has no battery sensor configured (show "N/A", not 100%).
  final bool batteryAvailable;
  // Navigation readiness + progress, reported by the Pi
  /// idle | sending | running | canceling | succeeded | failed | rejected | canceled | unavailable
  final String navState;
  final String? navError;
  final double? distanceRemaining;
  final bool navReady;
  final bool localized;
  final bool sim;

  const RobotState({
    required this.connection,
    required this.status,
    required this.pose,
    this.currentAction,
    this.destination,
    required this.rosRunning,
    required this.message,
    required this.nodeHealth,
    this.currentMission,
    this.bridgeStatus = const {},
    this.battery = 100,
    this.batteryVoltage = 12.6,
    this.batteryAlert = 'normal',
    this.batteryAvailable = true,
    this.navState = 'idle',
    this.navError,
    this.distanceRemaining,
    this.navReady = true,
    this.localized = true,
    this.sim = false,
  });

  /// Same data as [prev] but flagged as not connected (used when polling fails).
  factory RobotState.disconnected(RobotState prev, String message) => RobotState(
        connection: 'disconnected',
        status: prev.status,
        pose: prev.pose,
        currentAction: prev.currentAction,
        destination: prev.destination,
        rosRunning: prev.rosRunning,
        message: message,
        nodeHealth: prev.nodeHealth,
        currentMission: prev.currentMission,
        bridgeStatus: prev.bridgeStatus,
        battery: prev.battery,
        batteryVoltage: prev.batteryVoltage,
        batteryAlert: prev.batteryAlert,
        batteryAvailable: prev.batteryAvailable,
        navState: prev.navState,
        navError: prev.navError,
        distanceRemaining: prev.distanceRemaining,
        navReady: false,
        localized: prev.localized,
        sim: prev.sim,
      );

  factory RobotState.initial() => RobotState(
        connection: 'connecting',
        status: 'idle',
        pose: RobotPose.zero(),
        rosRunning: false,
        message: 'Connecting to backend...',
        nodeHealth: {},
        bridgeStatus: {},
        battery: 100,
        batteryVoltage: 12.6,
        batteryAlert: 'normal',
      );

  factory RobotState.fromJson(Map<String, dynamic> j) => RobotState(
        connection: j['connection'] ?? 'connected',
        status: j['status'] ?? 'idle',
        pose: j['pose'] != null
            ? RobotPose.fromJson(j['pose'] as Map<String, dynamic>)
            : RobotPose.zero(),
        currentAction: j['current_action'],
        destination: j['destination'],
        rosRunning: j['ros_running'] ?? false,
        message: j['message'] ?? '',
        nodeHealth: (j['node_health'] as Map<String, dynamic>? ?? {})
            .map((k, v) => MapEntry(k, v.toString())),
        currentMission: j['current_mission'],
        bridgeStatus: (j['bridge_status'] as Map<String, dynamic>? ?? {}),
        battery: (j['battery'] as num?)?.toInt() ?? 100,
        batteryVoltage: (j['battery_voltage'] as num?)?.toDouble() ?? 12.6,
        batteryAlert: j['battery_alert']?.toString() ?? 'normal',
        batteryAvailable: j['battery_available'] as bool? ?? (j['battery'] != null),
        navState: (j['nav'] as Map?)?['state']?.toString() ?? 'idle',
        navError: (j['nav'] as Map?)?['error']?.toString(),
        distanceRemaining: ((j['nav'] as Map?)?['distance_remaining'] as num?)?.toDouble(),
        navReady: j['nav_ready'] as bool? ?? true,
        localized: j['localized'] as bool? ?? true,
        sim: j['sim'] as bool? ?? false,
      );

  bool get isLaunching => status == 'launching';
  bool get isRunning => status == 'running';
  bool get isNavigating => status == 'navigating';
  bool get isTeleop => currentAction == 'teleop';
  bool get isReady => status == 'ready';
  bool get isError => status == 'error';
  /// True only when backend reports a live bridge (never treat empty status as OK).
  bool get bridgeVerified =>
      bridgeStatus['ok'] == true || bridgeStatus['ros_node_visible'] == true;
  /// START is enough to operate. READY / re-init is optional.
  bool get canOperate => rosRunning && !isLaunching;
  /// START is clickable whenever the robot is not already running.
  bool get canStart => isConnected && !rosRunning && !isLaunching;
  bool get isConnected => connection == 'connected';

  /// GO is only meaningful when Nav2 is active AND the robot knows where it is.
  bool get canNavigate => isConnected && canOperate && navReady && localized;

  /// Why navigation is not possible right now (null when it is).
  String? get navBlocker {
    if (!isConnected) return 'Not connected to the robot';
    if (!canOperate) return 'Start the robot first';
    if (!navReady) return 'Navigation (Nav2) is still starting…';
    if (!localized) return 'Set the robot position on the map first';
    return null;
  }

  /// True when battery level is below warning threshold.
  bool get isBatteryLow => batteryAlert == 'low' || batteryAlert == 'warning' || batteryAlert == 'critical';
  bool get isBatteryCritical => batteryAlert == 'critical';
}

class RobotPose {
  final double x;
  final double y;
  final double theta;

  const RobotPose({required this.x, required this.y, required this.theta});

  factory RobotPose.zero() => const RobotPose(x: 0, y: 0, theta: 0);

  factory RobotPose.fromJson(Map<String, dynamic> j) => RobotPose(
        x: (j['x'] as num?)?.toDouble() ?? 0,
        y: (j['y'] as num?)?.toDouble() ?? 0,
        theta: (j['theta'] as num?)?.toDouble() ?? 0,
      );

  @override
  String toString() =>
      'x: ${x.toStringAsFixed(2)}, y: ${y.toStringAsFixed(2)}, θ: ${theta.toStringAsFixed(1)}°';
}
