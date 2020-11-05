from functools import wraps
import debugger as dbg
from requester.utils.stream import PipeStreamHandler
from datetime import datetime
from requester.request import ffmpeg
import re

# REG_STEP = re.compile(r'\b(\w+)=\s*([-\+\w\.:/]+)\s*')
# REG_DONE = re.compile(r'\b([\w\s]+):\s*([-\+\w\.:/%]+)\s*')
REG_SIZE = re.compile(r'(\d+)([a-zA-Z]+)')
# REG_START = re.compile(r'\s*Press\s*\[q\]\sto\s*stop,\s*\[\?\]\s*for\s*help\s*')
# REG_META = re.compile(r'Metadata\s*:((\s*(\b\w+)\s*:\s*([^\n])+\n)+)')
# REG_INOUT = re.compile(r"""(?:Input|Output)\s*#(\d+)\s*,\s*(\w+)\s*,\s*(?:from|to)\s*['"](.*?)['"]:""")
REG_COPYRIGHT = re.compile(
    r"ffmpeg\s+version\s+(.*?)\s+Copyright\s+\(\w+\)\s+\d+-\d+\s+the\s+FFmpeg\s+\w+\s*"
)
REG_INPUT = re.compile(
    r"Input\s+#(\d+),\s+((?:\w+,)+)\s+from\s+'(.*?)':"
)
REG_METADATA = re.compile(
    r"Metadata:\s*\n((\s{4}(\w+)\s*:\s*\s(.*?)\n)+)"
)
REG_INPUT_EXTRA = re.compile(
    r"Duration:\s*([\d:\.]+)\s*, start:\s*([\w\.]+),\s*bitrate:\s*(.*?)\n"
)
REG_INPUT_STREAM = re.compile(
    r"Duration:\s*([\d:\.]+)\s*, start:\s*([\w\.]+),\s*bitrate:\s*(.*?)\n((?:\s{4}Stream\s*#(?:\d+):\d+(?:\(\w+\))?:\s*(?:.*?)\n(?:\s*Metadata:\s*\n(?:(?:\s{6}(?:\w+)\s*:\s*\s(?:.*?)\n)+))?)*)"
)
REG_OUTPUT = re.compile(
    r"Output\s+#(\d+),\s+((?:\w+,)+)\s+to\s+'(.*?'):"
)
REG_STREAM = re.compile(
    # r"Stream\s*#(\d+):\d+(?:\(\w+\))?:\s*(.*?)\n(\s*Metadata:\s*\n(\s{6}(\w+)\s*:\s*\s(.*?)\n)+)?"
    r"Stream\s*#(\d+):(\d+)(?:\(\w+\))?:\s*(.*?)\n(\s*Metadata:\s*\n(\s{6}(\w+)\s*:\s*\s(.*?)\n)+)?"
)

REG_STREAM_MAPPING = re.compile(
    r'Stream\s+mapping:'
)
# REG_FRAME_START = re.compile(
#     r"Press\s+\[q\]\s+to\s+stop,\s+\[\?\]\s+for\s+help"
# )
# REG_FRAME = re.compile(r'\b(\w+)=\s*([-\+\w\.:/]+)\s*')
# REG_FRAME_START = re.compile(
#     r'frame=\s*(.*?)\s*fps=\s*(.*?)\s*q=\s*(.*?)\s*L?size=\s*(.*?)\s*time=\s*(.*?)\s*bitrate=\s*(.*?)\s*speed=\s*(.*?)\s'
# )
REG_FRAME = re.compile(
    r'frame=\s*(.*?)\s*fps=\s*(.*?)\s*q=\s*(.*?)\s*L?size=\s*(.*?)\s*time=\s*(.*?)\s*bitrate=\s*(.*?)\s*speed=\s*(.*?)\s'
)
REG_FRAME_END = re.compile(
    r"video:\s*(\w+)\s+audio:\s*(\w+)\s+subtitle:\s*(\w+)\s+other streams:\s*(\w+)\s+global headers:\s*(\w+)\s+muxing overhead:\s*([\w\.%]+)"
)

CHECKPOINT_SEQUENCES = [
    lambda _, line: bool(REG_COPYRIGHT.match(line)),
    lambda _, line: bool(REG_INPUT.match(line)),
    lambda _, line: bool(REG_OUTPUT.match(line)),
    # lambda _, line: bool(REG_STREAM_MAPPING.match(line)),
    # lambda _, line: bool(REG_FRAME_START.match(line)),
    lambda _, line: bool(REG_FRAME.match(line)),
    lambda _, line: bool(REG_FRAME_END.match(line)),
]

CHECKPOINT_COPYRIGHT = 0
CHECKPOINT_INPUT = 1
CHECKPOINT_OUTPUT = 2
# CHECKPOINT_MAPPING = 3
# CHECKPOINT_FRAME = 4
# CHECKPOINT_RESULT = 5
CHECKPOINT_FRAME = 3
CHECKPOINT_RESULT = 4


def split_colon_keyword_dict(s):
    """
    Split a string into a dictionary of key / value pairs.

    Args:
        s: (str): write your description
    """
    retdict = {}
    for line in [i.strip() for i in s.split('\n') if i]:
        k, v = line.split(':', 1)
        k, v = k.strip(), v.strip()
        retdict[k] = v
    return retdict


class FFmpegStreamHandler(PipeStreamHandler):

    def __init__(self, process):
        """
        Initialize simulation.

        Args:
            self: (todo): write your description
            process: (todo): write your description
        """
        super().__init__(process)
        self.output_sequences = []
        self.cp_iter = iter(CHECKPOINT_SEQUENCES)

        self.checkpoint = next(self.cp_iter)

    def _get_frame(self):
        """
        Return a dictionary of frames

        Args:
            self: (todo): write your description
        """
        try:
            frame = self.output_sequences[CHECKPOINT_FRAME][-1]
        except IndexError:
            return {}
        else:
            frame, fps, q, size, time, bitrate, speed, = REG_FRAME.search(frame).groups()
            return {
                'frame': frame,
                'fps': fps,
                'q': q,
                'size': size,
                'bitrate': bitrate,
                'speed': speed
            }

    @staticmethod
    def _file_metadata(metadata_str):
        """
        Parse metadata file.

        Args:
            metadata_str: (str): write your description
        """
        metadata = REG_METADATA.search(metadata_str)
        metadata_dict = {}
        if metadata:
            filed, *_ = metadata.groups()
            metadata_dict = split_colon_keyword_dict(filed)
        return metadata_dict

    def get_inputs(self):
        """
        Returns a list of input_inputs

        Args:
            self: (todo): write your description
        """
        try:
            input_str = ''.join(self.output_sequences[CHECKPOINT_INPUT])
        except IndexError:
            return {}
        else:
            # 输入节点分割
            _, *valid_seq = REG_INPUT.split(input_str)
            input_lst = []
            for i in range(0, len(valid_seq), 4):
                index, formats, path, data = valid_seq[i: i+4]
                formats = [i for i in formats.split(',') if i]

                # input metadata
                metadata_dict = self._file_metadata(data)

                # input stream
                input_streams_result = REG_INPUT_STREAM.findall(data)
                input_stream_lst = []
                if input_streams_result:
                    for stream_result in input_streams_result:
                        duration, start, bitrate, stream_str = stream_result
                        # duration, start, bitrate, stream_id, stream_desc, stream_metas, *_ = stream_result
                        streams = []
                        for s in REG_STREAM.findall(stream_str):
                            stream_id, stream_index, stream_desc, stream_metas, *_ = s
                            streams.append({
                                'id': stream_id,
                                'index': stream_index,
                                'description': stream_desc.strip(),
                                'metadata': split_colon_keyword_dict(stream_metas),
                                'type': stream_desc.strip().split(':', 1)[0].lower(),
                            })

                        input_stream_lst.append({
                            'duration': duration,
                            'start': start,
                            'bitrate': bitrate.strip(),
                            'streams': streams
                        })

                input_lst.append({
                    'id': index,
                    'formats': formats,
                    'metadata': metadata_dict,
                    'streams': input_stream_lst
                })

            return input_lst

    def get_outputs(self):
        """
        Returns a dictionary of outputs for each dict

        Args:
            self: (todo): write your description
        """
        try:
            output_str = ''.join(self.output_sequences[CHECKPOINT_OUTPUT])
        except IndexError:
            return {}
        else:
            # 输出节点分割
            _, *valid_seq = REG_OUTPUT.split(output_str)
            output_lst = []
            for i in range(0, len(valid_seq), 4):
                index, formats, path, data = valid_seq[i: i + 4]
                formats = [i for i in formats.split(',') if i]

                # output metadata
                metadata_dict = self._file_metadata(data)

                # output stream
                output_streams_result = REG_STREAM.findall(data)
                output_stream_lst = []
                if output_streams_result:
                    for stream_result in output_streams_result:
                        stream_id, stream_index, stream_desc, stream_metas, *_ = stream_result
                        output_stream_lst.append({
                            'id': stream_id,
                            'index': stream_index,
                            'description': stream_desc.strip(),
                            'metadata': split_colon_keyword_dict(stream_metas),
                        })

                output_lst.append({
                    'id': index,
                    'formats': formats,
                    'metadata': metadata_dict,
                    'streams': output_stream_lst
                })

            return output_lst

    def speed(self):
        """
        Returns the speed.

        Args:
            self: (todo): write your description
        """
        frame_dict = self._get_frame()
        return frame_dict.get('speed', 'unknown')

    def size(self):
        """
        : return : class : frame size.

        Args:
            self: (todo): write your description
        """
        frame_dict = self._get_frame()
        return frame_dict.get('size', frame_dict.get('Lsize', 'unknown'))

    def complete_length(self):
        """
        Calculate the length of the task.

        Args:
            self: (todo): write your description
        """
        frame_dict = self._get_frame()
        tm = datetime.strptime(frame_dict.get('time', '00:00:00.00'), '%H:%M:%S.%f')
        time_length = tm.hour * 3600 + tm.minute * 60 + tm.second + tm.microsecond / 1e6
        return time_length

    def total_length(self):
        """
        Returns the total length of the queue.

        Args:
            self: (todo): write your description
        """
        self.get_inputs()
        return 0

    def complete_percent(self):
        """
        Returns the total percentage.

        Args:
            self: (todo): write your description
        """
        return self.complete_length() / self.total_length()

    def bitrate(self):
        """
        Returns the bitrate frame.

        Args:
            self: (todo): write your description
        """
        frame_dict = self._get_frame()
        return frame_dict.get('bitrate', 'unknown')

    def fps(self):
        """
        Return a dictionary of the frame.

        Args:
            self: (todo): write your description
        """
        frame_dict = self._get_frame()
        return frame_dict.get('fps', 'unknown')

    async def _stream_handler(self, stream_id, line):
          """
          Handler for streams

          Args:
              self: (todo): write your description
              stream_id: (int): write your description
              line: (str): write your description
          """
        if self.checkpoint(stream_id, line):
            self.output_sequences.append([])
            try:
                self.checkpoint = next(self.cp_iter)
            except StopIteration:
                # 剩余的不在checkpoint中的输出都保存在最后一个列表
                self.checkpoint = lambda *_: False
        self.output_sequences[-1].append(line)
        return True


def ffmpeg_operator(func=None, *, cal_len=True):
    """
    Decorator to call a function on a fixed - length operator.

    Args:
        func: (callable): write your description
        cal_len: (int): write your description
    """
    if func is None:
        def wrapper(func):
            """
            Decor function that returns a function to the result.

            Args:
                func: (callable): write your description
            """
            return ffmpeg_operator(func, cal_len=cal_len)
    else:
        @wraps(func)
        def wrapper(inputs, **kwargs):
            """
            Calculate fft.

            Args:
                inputs: (array): write your description
            """
            return ffmpeg(inputs, callable_cmd=func, cal_len=cal_len, **kwargs)

        # FFmpeg操作方法添加到ffmpeg请求器
        setattr(ffmpeg, func.__name__, wrapper)
    return wrapper


@ffmpeg_operator
async def cmdline(inputs, output, cmd, input_getter=None):
      """
      Format command output.

      Args:
          inputs: (array): write your description
          output: (todo): write your description
          cmd: (str): write your description
          input_getter: (todo): write your description
      """
    if input_getter is None:
        return cmd.format(*inputs, output=output)
    else:
        return cmd.format(inputs=input_getter(inputs), output=output)


@ffmpeg_operator
async def concat_av(inputs, output):
      """
      Concatenate a video.

      Args:
          inputs: (todo): write your description
          output: (str): write your description
      """
    video, audio = inputs
    cmd = ['-i', f'{video}',
           '-i', f'{audio}',
           '-vcodec', 'copy', '-acodec', 'copy',
           f'{output}']
    return cmd


@ffmpeg_operator
async def concat_demuxer(inputs, output):
      """
      Concatenate input fasta to hdf5.

      Args:
          inputs: (todo): write your description
          output: (todo): write your description
      """
    tempfile = dbg.tempdir.mktemp('.txt')
    concat_input = '\n'.join([f'file \'{input}\'' for input in inputs])

    with tempfile.open('w') as f:
        f.write(concat_input)

    cmd = ['-f', 'concat', '-safe', '0',
           '-i', f'{tempfile.filepath}', '-c', 'copy', f'{output}']
    return cmd


@ffmpeg_operator
async def concat_protocol(inputs, output):
      """
      Concatenate protobjs.

      Args:
          inputs: (todo): write your description
          output: (str): write your description
      """
    concat_input = '|'.join(inputs)
    cmd = ['-i', f'concat:\'{concat_input}\'', '-c', 'copy', f'{output}']
    return cmd


@ffmpeg_operator
async def convert(inputs, output, h265=False):
      """
      Convert inputs and output.

      Args:
          inputs: (todo): write your description
          output: (todo): write your description
          h265: (todo): write your description
      """
    input, *_ = inputs
    cmd = ['-i', input, '-y', '-qscale', '0', '-vcodec', 'libx264', output]
    return cmd


@ffmpeg_operator(cal_len=False)
async def information(inputs, **options):
      """
      Return a list of options.

      Args:
          inputs: (array): write your description
          options: (dict): write your description
      """
    cmd = []
    for input in inputs:
        cmd.extend(['-i', input])

    return cmd


async def cal_total_length(inputs, **options):
      """
      Calculate total total length of a video.

      Args:
          inputs: (array): write your description
          options: (dict): write your description
      """
    info = information(inputs)
    await info.start_request()
    all_inputs = info.get_data('input', {})
    length = 0
    for input in all_inputs:
        for stream in input.get('streams', []):
            for s in stream['streams']:
                if s['type'] == 'video':
                    has_video = True
                    break
            else:
                has_video = False

            if has_video:
                duration = datetime.strptime(stream['duration'], '%H:%M:%S.%f')
                length += (duration.hour * 3600 +
                           duration.minute * 60 +
                           duration.second +
                           duration.microsecond * 1e-6)
    return length


if __name__ == '__main__':
    from worker import init_workers
    init_workers()
    request = information([r'D:\znhys\0925\71\71_0.mp4', r'D:\znhys\0925\71\71_1.mp4'])
    request.start_request()
    print()