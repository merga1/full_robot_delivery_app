# Robot Controller — Flutter app

Tablet/phone controller for the robot. Talks to the Raspberry Pi API over HTTP
(see [../raspberry_pi/README.md](../raspberry_pi/README.md)).

## Build & run

```bash
flutter pub get
flutter analyze            # run this after pulling these changes — see "Verification" below
flutter test
flutter run                # debug, on a connected device
flutter build apk --release   # install on the tablet
```

Requirements: Flutter with Dart `^3.12`, Android SDK. If Gradle crashes with an out-of-memory
error (a `hs_err_pid*.log` file appears), raise the heap in `android/gradle.properties`:
`org.gradle.jvmargs=-Xmx3g`, and build with JDK 17 (`flutter config --jdk-dir <path>`).
Do not commit `hs_err_pid*.log` (now git-ignored).

**Release builds need the `INTERNET` permission** — it is now declared in
`android/app/src/main/AndroidManifest.xml` (previously only the debug manifest had it, so a
release APK could not reach the Pi at all).

## Connecting

The login screen discovers the Pi by UDP broadcast on port 9999 (answered by `pi_beacon.py`),
by mDNS (`_robotcontrol._tcp`), or by scanning; you can also type `http://<pi-ip>:8000`.
If the API is down the app can start it over SSH by running `~/raspberry_pi/start_backend.sh`
(the SSH password is used for that one action and is not stored).

## Screens

| Screen | What it does |
|---|---|
| Home | START / STOP, status card, mode switch **Navigation ↔ Joystick**, joystick pad, test drive / debug |
| Navigation map | Nav2's live map, destination markers, robot pose, **📍 Set robot position**, GO / Cancel, distance remaining |
| Missions | Multi-stop mission, per-step status, Cancel |
| Logs | Robot launch log with real severities |
| Settings | Idle logout, theme |

### How a navigation goal flows
1. Place a table: *Place / Reposition tables* → tap the map → saved on the Pi (`POST /destinations`).
2. Make sure the status bar has no orange warning (it names what is missing: start the robot,
   wait for Nav2, or set the robot position).
3. Select the destination → **GO** → the Pi asks Nav2 and answers *accepted* or an error that the
   app shows verbatim. While driving the status bar shows the distance left; when it ends you
   get *Arrived*, *Cancelled*, or the failure reason (before, a failure just reset the button).

## Networking behaviour (why it feels responsive now)

* One persistent `http.Client`; the status poll (1 s) never overlaps itself.
* Joystick: one command in flight; newer commands replace queued ones, so **stop is never
  stuck behind old "go" commands**. Release sends stop three times; the Pi also stops by itself
  0.5 s after the last command. Timeout for drive commands is 1 s.
* Three failed polls in a row ⇒ the status card turns *disconnected* (instead of freezing).

## Verification status

These Dart changes were written and reviewed carefully but **were not compiled in the environment
where they were produced** (no Flutter SDK there). Before using them:

```bash
flutter analyze && flutter test
```

and fix any trivial analyzer message (most likely an unused import or a lint). The backend they
talk to *was* run: its tests cover the exact JSON the screens read (`steps`, `progress`,
`nav`, `nav_ready`, `localized`, `battery_available`, …).

## Known limitations

* Battery shows "N/A" until you give the Pi a `ROBOT_BATTERY_TOPIC`.
* Roles: both `admin` and `user` can drive; restrict if you need it (`require_admin` exists).
* Polling, not WebSocket: status can be up to ~1 s old.
