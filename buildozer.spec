[app]
# (str) Title of your application
title = Aurora Grocery POS

# (str) Package name
package.name = aurora_grocery

# (str) Package domain (needed for android)
package.domain = org.example

# (str) Source code where the main.py live
source.dir = .
source.include_exts = py,png,jpg,kv,ini,md,json

# (str) Application versioning
version = 0.1

# (list) Application requirements
requirements = python3,kivy==2.2.2,kivymd==1.1.1

# (str) Supported orientation
orientation = portrait

# (int) Target Android API
android.api = 33

# (int) Minimum Android API required
android.minapi = 21

# (str) Android SDK version
android.sdk = 24

# (str) Android NDK version
android.ndk = 23b

# (bool) Android entry point, default is ok
android.entrypoint = org.kivy.android.PythonActivity

# (str) Android app version code
android.versioncode = 1

# (str) Android permissions
android.permissions = INTERNET

# (str) Presplash image
# presplash.filename = %(source.dir)s/data/presplash.png

# (str) Icon for application
# icon.filename = %(source.dir)s/data/icon.png

[buildozer]
log_level = 2
warn_on_root = 1
