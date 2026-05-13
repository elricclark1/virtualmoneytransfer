import os
import sys
import traceback

# Crash Handler Wrapper
try:
    import threading
    import time
    import webbrowser
    from kivy.config import Config
    from kivy.utils import platform

    # Set Window size for Desktop
    if platform not in ('android', 'ios'):
        Config.set('graphics', 'width', '1000')
        Config.set('graphics', 'height', '800')
        Config.set('graphics', 'resizable', True)

    # Ensure the current directory is in python path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    from kivy.app import App
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.label import Label
    from kivy.uix.image import Image
    from kivy.uix.button import Button
    from kivy.uix.screenmanager import ScreenManager, Screen
    from kivy.clock import Clock
    from kivy.graphics.texture import Texture
    from kivy.core.window import Window
    from kivy.graphics import Color, Rectangle, RoundedRectangle
    from kivy.uix.textinput import TextInput
    from kivy.uix.spinner import Spinner

    # Import the Flask app
    from app import app, get_local_ip, init_db

    # --- Custom UI Elements ---

    class RoundedButton(Button):
        def __init__(self, **kwargs):
            self.btn_color = kwargs.pop('color_rgb', (0.2, 0.6, 1, 1))
            super().__init__(**kwargs)
            self.background_color = (0, 0, 0, 0) # Invisible standard background
            self.background_normal = ''
            with self.canvas.before:
                Color(*self.btn_color)
                self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[15])
            self.bind(pos=self.update_rect, size=self.update_rect)

        def update_rect(self, *args):
            self.rect.pos = self.pos
            self.rect.size = self.size

    class StyledInput(TextInput):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.background_color = (0.2, 0.2, 0.25, 1)
            self.foreground_color = (1, 1, 1, 1)
            self.cursor_color = (0.2, 0.6, 1, 1)
            self.padding = [15, 15]
            self.font_size = '20sp'
            self.multiline = False
            self.halign = 'center'

    # --- Screens ---

    class MenuScreen(Screen):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            layout = BoxLayout(orientation='vertical', padding=40, spacing=30)
            
            title = Label(
                text="[b]SerPilas[/b]\nVirtual Money", 
                font_size='48sp', 
                halign='center', 
                markup=True,
                color=(1, 1, 1, 1),
                size_hint_y=0.4
            )
            
            btn_layout = BoxLayout(orientation='vertical', spacing=20, size_hint_y=0.6)
            
            btn_continue = RoundedButton(
                text="CONTINUE / IMPORT", 
                font_size='24sp', 
                bold=True,
                color_rgb=(0.1, 0.7, 0.3, 1), # Greenish
                size_hint_y=None, 
                height=80
            )
            btn_continue.bind(on_release=self.go_continue)
            
            btn_new = RoundedButton(
                text="NEW GAME", 
                font_size='24sp', 
                bold=True,
                color_rgb=(0.8, 0.2, 0.2, 1), # Reddish
                size_hint_y=None, 
                height=80
            )
            btn_new.bind(on_release=self.go_new)
            
            btn_layout.add_widget(btn_continue)
            btn_layout.add_widget(btn_new)
            
            layout.add_widget(title)
            layout.add_widget(btn_layout)
            self.add_widget(layout)

        def go_continue(self, instance):
            # Starts server and goes to admin dashboard for continue/import
            self.manager.current = 'server'
            self.manager.get_screen('server').start_server(reset_db=False, host_username='Host')

        def go_new(self, instance):
            self.manager.current = 'setup' # Go to setup first

    class SetupScreen(Screen):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            layout = BoxLayout(orientation='vertical', padding=40, spacing=20)
            
            layout.add_widget(Label(text="Game Setup", font_size='32sp', bold=True, size_hint_y=0.2))
            
            # Host Name
            layout.add_widget(Label(text="Host Username", color=(0.7, 0.7, 0.7, 1), size_hint_y=None, height=30))
            self.username_input = StyledInput(hint_text="Enter your name", size_hint_y=None, height=60)
            layout.add_widget(self.username_input)
            
            # Game Mode
            layout.add_widget(Label(text="Game Mode", color=(0.7, 0.7, 0.7, 1), size_hint_y=None, height=30))
            self.mode_spinner = Spinner(
                text='Monopoly Mode',
                values=('Monopoly Mode', 'Custom Mode'),
                size_hint_y=None,
                height=60,
                background_normal='',
                background_color=(0.2, 0.4, 0.6, 1),
                font_size='18sp',
                bold=True
            )
            layout.add_widget(self.mode_spinner)
            
            layout.add_widget(Label(size_hint_y=0.1)) # Spacer
            
            btn_start = RoundedButton(
                text="START SERVER",
                font_size='22sp',
                bold=True,
                color_rgb=(0.2, 0.8, 0.4, 1),
                size_hint_y=None, height=80
            )
            btn_start.bind(on_release=self.start_game)
            layout.add_widget(btn_start)
            
            self.add_widget(layout)

        def start_game(self, instance):
            username = self.username_input.text.strip()
            if not username:
                self.username_input.hint_text = "Name required!"
                self.username_input.hint_text_color = (1, 0, 0, 1)
                return
                
            mode = 'monopoly' if self.mode_spinner.text == 'Monopoly Mode' else 'custom'
            
            self.manager.current = 'server'
            self.manager.get_screen('server').start_server(
                reset_db=True, 
                host_username=username, 
                game_mode=mode
            )

    class ServerScreen(Screen):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.server_started = False
            self.server_thread = None
            self.flask_app = app
            self.ip_address = "Detecting..."
            self.host_username = None
            
            layout = BoxLayout(orientation='vertical', padding=20, spacing=20)
            
            self.status_label = Label(
                text="Initializing Server...", 
                font_size='24sp', 
                bold=True, 
                color=(0.2, 0.8, 1, 1),
                size_hint_y=0.15
            )
            
            self.qr_image = Image(size_hint_y=0.5, allow_stretch=True)
            
            self.ip_label = Label(
                text="", 
                font_size='32sp', 
                halign='center', 
                bold=True,
                size_hint_y=0.2
            )
            
            instr = Label(
                text="Scan QR code with other phones\nto join the game", 
                font_size='16sp', 
                color=(0.7, 0.7, 0.7, 1),
                size_hint_y=0.15
            )

            btn_relaunch = RoundedButton(
                text="RE-OPEN GAME",
                size_hint_y=None, height=50,
                font_size='14sp',
                color_rgb=(0.2, 0.3, 0.5, 1)
            )
            btn_relaunch.bind(on_release=self.open_browser)
            
            layout.add_widget(self.status_label)
            layout.add_widget(self.qr_image)
            layout.add_widget(self.ip_label)
            layout.add_widget(instr)
            layout.add_widget(btn_relaunch)
            
            self.add_widget(layout)

        def start_server(self, reset_db=False, host_username=None, game_mode='monopoly'):
            if self.server_started:
                if host_username:
                     self.host_username = host_username
                     self.open_browser(None)
                return

            self.status_label.text = "Configuring World..."
            self.host_username = host_username
            
            # Database Setup Logic
            from app import User, SystemSetting, db
            
            if reset_db:
                try:
                    db_path = os.path.join(os.path.dirname(__file__), 'currency.db')
                    if os.path.exists(db_path):
                        os.remove(db_path)
                    print("Database reset.")
                except Exception as e:
                    print(f"Error resetting DB: {e}")

            # Initialize Database Structure
            init_db()
            
            # Apply Game Mode Settings immediately
            if reset_db:
                with self.flask_app.app_context():
                    is_monopoly = '1' if game_mode == 'monopoly' else '0'
                    setting = SystemSetting.query.get('monopoly_mode')
                    if not setting:
                        setting = SystemSetting(key='monopoly_mode', value=is_monopoly)
                        db.session.add(setting)
                    else:
                        setting.value = is_monopoly
                    
                    if is_monopoly == '1':
                        if not User.query.filter_by(username='Free Parking').first():
                            fp = User(username='Free Parking', balance=0, color_hex='#EF4444')
                            db.session.add(fp)
                    
                    db.session.commit()

            # Start Flask in a separate thread
            self.server_thread = threading.Thread(target=self.run_flask)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            self.server_started = True
            
            Clock.schedule_interval(self.update_ui_info, 2)
            Clock.schedule_once(self.update_ui_info, 0.5)
            
            if host_username:
                 Clock.schedule_once(lambda dt: self.open_browser(None), 2)

        def run_flask(self):
            try:
                self.flask_app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)
            except Exception as e:
                print(f"Flask Server Error: {e}")

        def update_ui_info(self, dt):
            ip = get_local_ip()
            if ip != self.ip_address:
                self.ip_address = ip
                self.status_label.text = "SERVER ONLINE"
                self.status_label.color = (0, 1, 0, 1)
                self.ip_label.text = f"http://{ip}:8080"
                self.generate_qr_texture(f"http://{ip}:8080")

        def generate_qr_texture(self, data):
            import qrcode
            from io import BytesIO
            from kivy.core.image import Image as CoreImage
            
            qr = qrcode.QRCode(box_size=10, border=2)
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="white", back_color="transparent")
            
            buffer = BytesIO()
            img.save(buffer, format='png')
            buffer.seek(0)
            
            im = CoreImage(buffer, ext='png')
            self.qr_image.texture = im.texture
            
        def open_browser(self, instance):
            if self.host_username:
                 webbrowser.open(f"http://127.0.0.1:8080/auto_login/{self.host_username}")
            else:
                 webbrowser.open("http://127.0.0.1:8080/")

    class ServerApp(App):
        def build(self):
            Window.clearcolor = (0.05, 0.05, 0.1, 1)
            sm = ScreenManager()
            sm.add_widget(MenuScreen(name='menu'))
            sm.add_widget(SetupScreen(name='setup'))
            sm.add_widget(ServerScreen(name='server'))
            return sm

    if __name__ == '__main__':
        ServerApp().run()

except Exception:
    # --- Emergency Crash Reporter ---
    # If anything above fails (imports, app startup), this block catches it
    # and displays the full traceback on the screen.
    from kivy.app import App
    from kivy.uix.label import Label
    from kivy.uix.scrollview import ScrollView
    from kivy.base import runTouchApp
    from kivy.uix.boxlayout import BoxLayout
    
    error_msg = traceback.format_exc()
    
    class CrashApp(App):
        def build(self):
            from kivy.core.window import Window
            layout = BoxLayout(orientation='vertical', padding=10)
            layout.add_widget(Label(text="CRASH DETECTED", font_size='24sp', bold=True, color=(1,0,0,1), size_hint_y=None, height=60))
            
            scroll = ScrollView(do_scroll_x=False)
            # Use text_size to enable wrapping and halign for left alignment
            label = Label(
                text=error_msg, 
                size_hint_y=None, 
                color=(1,1,1,1),
                font_size='12sp',
                font_name='Roboto',
                halign='left',
                valign='top',
                text_size=(Window.width - 20, None)
            )
            label.bind(texture_size=label.setter('size'))
            scroll.add_widget(label)
            
            layout.add_widget(scroll)
            return layout

    # Try to run the crash reporter
    try:
        CrashApp().run()
    except:
        # If even Kivy fails, fallback to printing to a file (if writable)
        # This is a last resort.
        with open("crash_log.txt", "w") as f:
            f.write(error_msg)
