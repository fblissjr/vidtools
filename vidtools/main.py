import orjson

from . import operations
from . import presets

# --- FFmpeg Function Handlers ---

def resize_video_handler(args):
    operations.resize_video(args.input, args.output, percentage=args.percentage,
                            width=args.width, height=args.height, algorithm=args.algorithm)

def convert_format_handler(args):
    operations.convert_format(args.input, args.output, args.format, args.vcodec, args.acodec,
                              args.vbitrate, args.abitrate, args.crf, args.start_time,
                              args.end_time, args.duration, args.preset, args.copy)

def extract_audio_handler(args):
    operations.extract_audio(args.input, args.output, args.format, args.start_time,
                             args.end_time, args.duration)

def extract_frames_handler(args):
    operations.extract_frames(args.input, args.output, args.rate, args.format, args.start_time,
                              args.end_time, args.duration)

def concatenate_videos_handler(args):
    use_copy = args.copy and not args.reencode
    operations.concatenate_videos(args.inputs, args.output, use_copy=use_copy)

def crop_video_handler(args):
    operations.crop_video(args.input, args.output, args.width, args.height, args.x, args.y)

def get_video_info_handler(args):
    info = operations.get_video_info(args.input)
    print(orjson.dumps(info, option=orjson.OPT_INDENT_2).decode())

def add_subtitles_handler(args):
    operations.add_subtitles(args.input, args.output, args.subs)

def rotate_video_handler(args):
    operations.rotate_video(args.input, args.output, args.rotation)

def apply_preset_handler(args):
    operations.apply_preset(args.input, args.output, args.name)

def cut_video_handler(args):
    operations.cut_video(args.input, args.output, start_time=args.start_time, end_time=args.end_time,
                         duration=args.duration, use_copy=not args.accurate, fast_seek=args.fast_seek,
                         fix_sync=args.fix_sync, video_codec=args.video_codec,
                         audio_codec=args.audio_codec, crf=args.crf)

def sanitize_video_handler(args):
    operations.sanitize_video(input_file=args.input, output_file=args.output, limit=args.limit,
                              noise=args.noise, crf=args.crf, extra_bottom=args.extra_bottom,
                              manual_crop=args.manual_crop,
                              audio_mode=("none" if args.no_audio else "aac" if args.aac else "copy"))

def slideshow_handler(args):
    operations.create_slideshow(args.pattern, args.output, args.duration, args.fps,
                                size=args.size,
                                audio_file=args.audio,
                                resize_mode=args.resize_mode,
                                pad_color=args.pad_color,
                                sanitize_crop=args.sanitize_crop,
                                sanitize_noise=args.sanitize_noise)

# --- Preset Command Handlers ---

def list_presets_handler(args):
    presets.list_presets()

def delete_preset_handler(args):
    presets.delete_preset(args.name)

def edit_preset_handler(args):
    presets.edit_preset_command(args.name)


def add_audio_handler(args):
    mode = "mix" if args.mix else "replace"
    operations.add_audio(args.input, args.audio, args.output,
                         mode=mode, volume=args.volume)

def create_srt_from_transcript_handler(args):
    operations.create_srt_from_transcript(args.input, args.output)
