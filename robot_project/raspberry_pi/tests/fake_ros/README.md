Minimal stand-ins for rclpy and the message packages, used ONLY by
tests/test_ros_wiring.py to execute app/ros_hub.py's rclpy node code without a
ROS install.  They mirror the rclpy API shapes the hub uses; they are not a
substitute for testing on the real robot (see ../../README.md, "On-robot checklist").
