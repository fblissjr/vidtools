from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button, Input, Label, Dropdown
from textual.containers import Container, Vertical, Horizontal
import cli  # Import cli module
import main # Import main module to call ffmpeg functions
import os
import asyncio # For asynchronous operations

class FFmpegTUI(App):
    """Textual TUI for FFmpeg Tool."""

    CSS_PATH = "tui.tcss"
    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("l", "list_presets_tui", "List Presets"),
        ("r", "set_operation('resize')", "Resize"),
        ("c", "set_operation('convert')", "Convert"),
        ("e", "set_operation('extract_audio')", "Extract Audio"),
        ("f", "set_operation('extract_frames')", "Extract Frames"),
        ("x", "set_operation('crop')", "Crop"),
        ("o", "set_operation('rotate')", "Rotate"),
        ("s", "set_operation('subtitles')", "Subtitles"),
        ("n", "set_operation('concatenate')", "Concatenate"),
        ("i", "set_operation('info')", "Info"),
        ("p", "set_operation('preset')", "Apply Preset"),
        ("S", "save_preset_tui", "Save Preset"),
        ("D", "delete_preset_tui", "Delete Preset"),
        ("E", "edit_preset_tui", "Edit Preset"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_operation = None
        self.preset_dropdown = None
        self.output_area = None
        self.ffmpeg_process = None # To store the ffmpeg subprocess

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Container(id="app-grid"):
            with Vertical(id="operations-panel", classes="panel"):
                yield Label("Operations", id="operations-label")
                yield Button("Resize", id="resize-button", action="set_operation('resize')")
                yield Button("Convert", id="convert-button", action="set_operation('convert')")
                yield Button("Extract Audio", id="extract-audio-button", action="set_operation('extract_audio')")
                yield Button("Extract Frames", id="extract-frames-button", action="set_operation('extract_frames')")
                yield Button("Crop", id="crop-button", action="set_operation('crop')")
                yield Button("Rotate", id="rotate-button", action="set_operation('rotate')")
                yield Button("Subtitles", id="subtitles-button", action="set_operation('subtitles')")
                yield Button("Concatenate", id="concatenate-button", action="set_operation('concatenate')")
                yield Button("Get Info", id="info-button", action="set_operation('info')")
                yield Button("Apply Preset", id="apply-preset-button", action="set_operation('preset')")
                yield Button("List Presets", id="list-presets-button", action="list_presets_tui")
                yield Button("Save Preset", id="save-preset-button", action="save_preset_tui")
                yield Button("Delete Preset", id="delete-preset-button", action="delete_preset_tui")
                yield Button("Edit Preset", id="edit-preset-button", action="edit_preset_tui")

            with Vertical(id="input-panel", classes="panel"):
                yield Label("Parameters", id="parameters-label")
                yield Static(id="operation-description", content="Select an operation to see parameters.")
                yield Container(id="input-fields-container")

            with Vertical(id="output-panel", classes="panel"):
                yield Label("Output", id="output-label")
                self.output_area = Static(id="output-display", content="", wrap=True, markup=False) # wrap=True and markup=False for plain text output
                yield self.output_area
                yield Button("Run", id="run-button", disabled=True, action="run_operation")

    def on_mount(self) -> None:
        self.preset_dropdown = Dropdown("Loading presets...", [])
        self.query_one("#input-fields-container").mount(self.preset_dropdown)
        self.update_preset_dropdown()


    def action_quit_app(self) -> None:
        self.exit()

    def action_list_presets_tui(self) -> None:
        print("\n--- Presets (from TUI Action) ---")
        cli.handle_command(cli.parse_arguments(["list_presets"]))
        self.update_preset_dropdown()


    def update_preset_dropdown(self):
        presets_dict = main.PRESETS
        preset_names = list(presets_dict.keys())
        dropdown_options = [(name, name) for name in preset_names]
        self.preset_dropdown.set_options(dropdown_options)
        if not dropdown_options:
            self.preset_dropdown.set_placeholder("No presets available")
        else:
            self.preset_dropdown.set_placeholder("Select a preset")


    def action_set_operation(self, operation_name: str) -> None:
        self.current_operation = operation_name
        self.query_one("#operation-description").content = f"Parameters for: {operation_name.replace('_', ' ').title()}"
        input_fields_container = self.query_one("#input-fields-container")
        input_fields_container.remove_children()
        self.query_one("#run-button").disabled = False

        # ... (rest of action_set_operation - input field mounting logic - same as before)
        if operation_name == "resize":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="resize-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output File:", classes="input-label"),
                Input(id="resize-output-file", classes="input-field", placeholder="Path for output video"),
                Label("Resize Percentage (e.g., 0.5):", classes="input-label"),
                Input(id="resize-percentage", classes="input-field", placeholder="0.5", type="float"),
                Label("Scaling Algorithm:", classes="input-label"),
                Dropdown("lanczos", [("neighbor", "neighbor"), ("bilinear", "bilinear"), ("lanczos", "lanczos")], id="resize-algorithm-dropdown")
            )
        elif operation_name == "convert":
             input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="convert-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output File:", classes="input-label"),
                Input(id="convert-output-file", classes="input-field", placeholder="Path for output file"),
                Label("Format:", classes="input-label"),
                Dropdown("mp4", [("mp4", "MP4"), ("gif", "GIF"), ("mp3", "MP3"), ("webm", "WebM"), ("avi", "AVI")], id="convert-format-dropdown"),
                Label("Video Codec (optional):", classes="input-label"),
                Input(id="convert-vcodec", classes="input-field", placeholder="libx264"),
                Label("Audio Codec (optional):", classes="input-label"),
                Input(id="convert-acodec", classes="input-field", placeholder="aac"),
                Label("Quality (optional):", classes="input-label"),
                Input(id="convert-quality", classes="input-field", placeholder="28", type="str")
            )
        elif operation_name == "extract_audio":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="extract-audio-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output File:", classes="input-label"),
                Input(id="extract-audio-output-file", classes="input-field", placeholder="Path for output audio file"),
                Label("Audio Format (optional, default: copy):", classes="input-label"),
                Input(id="extract-audio-aformat", classes="input-field", placeholder="copy")
            )
        elif operation_name == "extract_frames":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="extract-frames-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output Pattern:", classes="input-label"),
                Input(id="extract-frames-output-pattern", classes="input-field", placeholder="frame%04d.jpg"),
                Label("Frame Rate (fps, default: 1):", classes="input-label"),
                Input(id="extract-frames-rate", classes="input-field", placeholder="1", type="float"),
                Label("Image Format (default: image2):", classes="input-label"),
                Input(id="extract-frames-iformat", classes="input-field", placeholder="image2")
            )
        elif operation_name == "crop":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="crop-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output File:", classes="input-label"),
                Input(id="crop-output-file", classes="input-field", placeholder="Path for output video"),
                Label("Crop Width:", classes="input-label"),
                Input(id="crop-width", classes="input-field", placeholder="640", type="int"),
                Label("Crop Height:", classes="input-label"),
                Input(id="crop-height", classes="input-field", placeholder="480", type="int"),
                Label("Crop X Offset:", classes="input-label"),
                Input(id="crop-x", classes="input-field", placeholder="0", type="int"),
                Label("Crop Y Offset:", classes="input-label"),
                Input(id="crop-y", classes="input-field", placeholder="0", type="int")
            )
        elif operation_name == "rotate":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="rotate-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output File:", classes="input-label"),
                Input(id="rotate-output-file", classes="input-field", placeholder="Path for output video"),
                Label("Rotation Angle:", classes="input-label"),
                Dropdown("90", [("90", "90 degrees"), ("180", "180 degrees"), ("270", "270 degrees")], id="rotate-rotation-dropdown")
            )
        elif operation_name == "subtitles":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="subtitles-input-file", classes="input-field", placeholder="Path to input video"),
                Label("Output File:", classes="input-label"),
                Input(id="subtitles-output-file", classes="input-field", placeholder="Path for output video with subtitles"),
                Label("Subtitles File:", classes="input-label"),
                Input(id="subtitles-subs-file", classes="input-field", placeholder="Path to subtitles file (.srt, .ass, etc.)")
            )
        elif operation_name == "concatenate":
            input_fields_container.mount(
                Label("Output File:", classes="input-label"),
                Input(id="concatenate-output-file", classes="input-field", placeholder="Path for output concatenated video"),
                Label("Input Files (space-separated):", classes="input-label"),
                Input(id="concatenate-input-files", classes="input-field", placeholder="input1.mp4 input2.mov ...")
            )
        elif operation_name == "info":
            input_fields_container.mount(
                Label("Input File:", classes="input-label"),
                Input(id="info-input-file", classes="input-field", placeholder="Path to input video for info")
            )
        elif operation_name == "preset":
            input_fields_container.mount(
                Label("Apply Preset:", classes="input-label"),
                self.preset_dropdown
            )
        else:
            input_fields_container.mount(Static("No parameters for this operation.", id="no-params-message"))
            self.query_one("#run-button").disabled = True


    async def action_run_operation(self) -> None: # Make action_run_operation async
        """Runs the ffmpeg operation based on current operation and input fields."""
        if not self.current_operation:
            self.output_area.content = "Error: No operation selected."
            return

        command_args_list = [self.current_operation]
        output_text = ""
        input_file = "" # Initialize input_file to be used in try-finally block

        try: # Use try-finally to ensure button enabling/disabling
            self.query_one("#run-button").disabled = True # Disable Run button during operation
            self.output_area.content = "" # Clear previous output

            if self.current_operation == "resize":
                input_file = self.query_one("#resize-input-file", Input).value
                output_file = self.query_one("#resize-output-file", Input).value
                percentage = self.query_one("#resize-percentage", Input).value
                algorithm = self.query_one("#resize-algorithm-dropdown", Dropdown).value

                if not input_file or not output_file:
                    self.output_area.content = "Error: Input and Output files are required for resize."
                    return

                command_args_list.extend([input_file, output_file])
                if percentage:
                    command_args_list.extend(["-p", str(percentage)])
                if algorithm and algorithm != "lanczos":
                    command_args_list.extend(["--algorithm", algorithm])

            elif self.current_operation == "convert":
                input_file = self.query_one("#convert-input-file", Input).value
                output_file = self.query_one("#convert-output-file", Input).value
                format_type = self.query_one("#convert-format-dropdown", Dropdown).value
                vcodec = self.query_one("#convert-vcodec", Input).value
                acodec = self.query_one("#convert-acodec", Input).value
                quality = self.query_one("#convert-quality", Input).value

                if not input_file or not output_file or not format_type:
                    self.output_area.content = "Error: Input File, Output File, and Format are required for convert."
                    return

                command_args_list.extend([input_file, output_file, format_type])
                if vcodec: command_args_list.extend(["--vcodec", vcodec])
                if acodec: command_args_list.extend(["--acodec", acodec])
                if quality: command_args_list.extend(["--quality", quality])

            elif self.current_operation == "extract_audio":
                input_file = self.query_one("#extract-audio-input-file", Input).value
                output_file = self.query_one("#extract-audio-output-file", Input).value
                aformat = self.query_one("#extract-audio-aformat", Input).value

                if not input_file or not output_file:
                    self.output_area.content = "Error: Input and Output files are required for extract audio."
                    return
                command_args_list.extend([input_file, output_file])
                if aformat and aformat != "copy": command_args_list.extend(["--aformat", aformat])

            elif self.current_operation == "extract_frames":
                input_file = self.query_one("#extract-frames-input-file", Input).value
                output_pattern = self.query_one("#extract-frames-output-pattern", Input).value
                rate = self.query_one("#extract-frames-rate", Input).value
                iformat = self.query_one("#extract-frames-iformat", Input).value

                if not input_file or not output_pattern:
                    self.output_area.content = "Error: Input File and Output Pattern are required for extract frames."
                    return
                command_args_list.extend([input_file, output_pattern])
                if rate and rate != 1: command_args_list.extend(["-r", str(rate)])
                if iformat and iformat != "image2": command_args_list.extend(["--iformat", iformat])

            elif self.current_operation == "crop":
                input_file = self.query_one("#crop-input-file", Input).value
                output_file = self.query_one("#crop-output-file", Input).value
                width = self.query_one("#crop-width", Input).value
                height = self.query_one("#crop-height", Input).value
                x = self.query_one("#crop-x", Input).value
                y = self.query_one("#crop-y", Input).value

                if not input_file or not output_file or not width or not height or not x or not y:
                    self.output_area.content = "Error: All crop parameters are required."
                    return
                command_args_list.extend([input_file, output_file, str(width), str(height), str(x), str(y)])

            elif self.current_operation == "rotate":
                input_file = self.query_one("#rotate-input-file", Input).value
                output_file = self.query_one("#rotate-output-file", Input).value
                rotation = self.query_one("#rotate-rotation-dropdown", Dropdown).value

                if not input_file or not output_file or not rotation:
                    self.output_area.content = "Error: Input File, Output File, and Rotation are required for rotate."
                    return
                command_args_list.extend([input_file, output_file, rotation])

            elif self.current_operation == "subtitles":
                input_file = self.query_one("#subtitles-input-file", Input).value
                output_file = self.query_one("#subtitles-output-file", Input).value
                subs_file = self.query_one("#subtitles-subs-file", Input).value

                if not input_file or not output_file or not subs_file:
                    self.output_area.content = "Error: Input File, Output File, and Subtitles File are required for subtitles."
                    return
                command_args_list.extend([input_file, output_file, subs_file])

            elif self.current_operation == "concatenate":
                output_file = self.query_one("#concatenate-output-file", Input).value
                input_files_str = self.query_one("#concatenate-input-files", Input).value

                if not output_file or not input_files_str:
                    self.output_area.content = "Error: Output File and Input Files are required for concatenate."
                    return

                input_files = input_files_str.split()
                validated_input_files = []
                for file in input_files:
                    if not os.path.exists(file) or not os.path.isfile(file):
                        self.output_area.content = f"Error: Input file '{file}' not found or not a file."
                        return
                    validated_input_files.append(file)

                if not validated_input_files:
                    self.output_area.content = "Error: No valid input files provided for concatenation."
                    return

                command_args_list.extend([output_file] + validated_input_files)

            elif self.current_operation == "info":
                input_file = self.query_one("#info-input-file", Input).value
                if not input_file:
                    self.output_area.content = "Error: Input File is required for Get Info."
                    return
                command_args_list.extend([input_file])

            elif self.current_operation == "preset":
                preset_name = self.preset_dropdown.value
                input_file = self.query_one("#preset-input-file", Input).value
                output_file = self.query_one("#preset-output-file", Input).value

                if not preset_name or not input_file or not output_file:
                    self.output_area.content = "Error: Preset, Input File and Output File are required for Apply Preset."
                    return
                command_args_list.extend([input_file, output_file, preset_name])


            if command_args_list:
                command_str = " ".join(["python main.py"] + command_args_list)
                self.output_area.content = f"Running command: {command_str}\n\n"

                # *** Run ffmpeg and redirect output to TUI ***
                process = await asyncio.create_subprocess_exec(
                    "python", "main.py", *command_args_list, # Run main.py as subprocess
                    stdout=asyncio.subprocess.PIPE, # Capture stdout
                    stderr=asyncio.subprocess.PIPE  # Capture stderr
                )
                self.ffmpeg_process = process # Store process for potential future use (e.g., cancellation)

                # Asynchronously read and display output
                async def display_output(stream, update_method): # Generic display function
                    while True:
                        line = await stream.readline() # Read line asynchronously
                        if not line: # End of stream
                            break
                        decoded_line = line.decode().strip() # Decode bytes to string
                        update_method(decoded_line + "\n") # Update TUI output area

                # Create tasks to read stdout and stderr concurrently
                stdout_task = asyncio.create_task(display_output(process.stdout, self.update_output_area))
                stderr_task = asyncio.create_task(display_output(process.stderr, self.update_output_area))

                await asyncio.gather(stdout_task, stderr_task) # Wait for both tasks to complete
                await process.wait() # Wait for process to finish

                self.output_area.content += f"\nFFmpeg operation completed with return code: {process.returncode}."

        finally: # Ensure Run button is re-enabled even if errors occur
            self.query_one("#run-button").disabled = False # Re-enable Run button after operation

    def update_output_area(self, new_text):
        """Updates the output area with new text, appending to existing content."""
        current_content = self.output_area.content
        self.output_area.content = current_content + new_text


    def action_save_preset_tui(self) -> None:
        print("\n--- Save Preset (TUI Action) ---")
        cli.handle_command(cli.parse_arguments(["save_preset", "my_tui_preset"]))


    def action_delete_preset_tui(self) -> None:
        print("\n--- Delete Preset (TUI Action) ---")
        cli.handle_command(cli.parse_arguments(["delete_preset", "my_tui_preset"]))
        self.update_preset_dropdown()

    def action_edit_preset_tui(self) -> None:
        print("\n--- Edit Preset (TUI Action) ---")
        cli.handle_command(cli.parse_arguments(["edit_preset", "my_tui_preset"]))


if __name__ == "__main__":
    app = FFmpegTUI()
    app.run()