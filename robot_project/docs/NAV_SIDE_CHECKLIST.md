# Navigation-side checklist (`articubot_one1`)

The API/app can only be as good as the ROS side they talk to. The workspace archive that was
shared for the review contained 0-byte files, so these items could **not** be checked and are
listed here so you can verify them on the Pi. Run `bash raspberry_pi/drive_diagnose.sh` first —
it prints most of the evidence below in one go.

## 1. The stack really comes up
`ros2 launch articubot_one master.launch.py` should bring up, at least: `controller_manager`,
`amcl`, `map_server`, `bt_navigator`, `controller_server`, `planner_server`, `twist_mux`.
If your launch uses different node names, set `ROBOT_EXPECTED_NODES` in `real1_link.env`
(otherwise the app waits `ROBOT_LAUNCH_GRACE_S` = 45 s before treating the stack as running).

## 2. Nav2 is *active*
```
ros2 lifecycle get /bt_navigator        # → active [3]
ros2 action list | grep navigate_to_pose
```
Nodes stuck in *unconfigured/inactive* usually mean the map failed to load or `lifecycle_manager`
timed out (slow Pi). Check `bond_timeout` / `autostart: true` in `nav2_params.yaml`.

## 3. Localization
AMCL needs an initial pose. The app's 📍 button publishes it; alternatively your launch may run
`set_initial_pose` / `home_pose.yaml`. Verify:
```
ros2 topic echo /amcl_pose --once
ros2 run tf2_ros tf2_echo map base_link      # must print a transform, not "frame does not exist"
```

## 4. One map, everywhere
`localization_launch` must load the same file you see in the app. The workspace has both
`my_map.yaml` and `my_map1.yaml`:
```
ros2 param get /map_server yaml_filename
```
The app now draws Nav2's *live* `/map`, so goals can't land on a different map; but if you
re-save a map, re-place your table destinations.

## 5. `/cmd_vel` routing (twist_mux)
The API's joystick publishes on `/cmd_vel` (`REAL1_CMD_VEL_TOPIC`); Nav2 also publishes velocity
commands. They must reach the motors through `twist_mux` with sensible priorities, and
the mux output must be what `diff_drive_controller` listens to.
```
ros2 topic info /cmd_vel -v          # who publishes / subscribes
ros2 param list /twist_mux           # inputs, priorities, timeouts
```
Typical layout: Nav2 → `cmd_vel_nav` (priority 10), joystick/app → `cmd_vel_joy` (priority 100),
mux output → controller. If you use that layout, set `REAL1_CMD_VEL_TOPIC=/cmd_vel_joy`.
Check units: the app sends m/s and rad/s; use `REAL1_CMD_VEL_LINEAR_SCALE/ANGULAR_SCALE`
only if your controller expects something else.

## 6. Time and frames
`use_sim_time` must be `false` on the real robot in every Nav2 node. Goal frame is `map`;
`base_link`, `odom`, `map` TF tree must be connected.

## 7. Robustness items worth doing
* Remove `StartMission.srv` / `mission_manager` if you keep using the API's mission runner
  (two mission systems is one too many), or switch the API to call yours — pick one.
* Give the Pi a fixed IP / DHCP reservation and set `ROS_DOMAIN_ID` consistently in
  `real1_link.env`'s environment and in any terminal you debug from.
* Add a hardware e-stop independent of ROS; software stop is not a safety function.
