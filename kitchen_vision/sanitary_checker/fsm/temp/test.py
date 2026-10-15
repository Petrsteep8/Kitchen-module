import subprocess
import time
import psutil


def get_cpu_load():
  # interval=1 замеряет загрузку за 1 секунду для точности
  return psutil.cpu_percent(interval=1)


def get_gpu_load():
  try:
    result = subprocess.check_output([
        'nvidia-smi',
        '--query-gpu=utilization.gpu,memory.used,memory.total',
        '--format=csv,nounits,noheader',
    ],
                                     encoding='utf-8')
    line = result.strip().split('\n')[0]
    gpu_util, mem_used, mem_total = map(str.strip, line.split(','))
    return f'{gpu_util}% (Память: {mem_used} / {mem_total} MiB)'
  except FileNotFoundError:
    return 'GPU NVIDIA не найдена или нет nvidia-smi'


if __name__ == '__main__':
  print('Сбор данных о системе...')
  print(f'Загрузка CPU: {get_cpu_load()}%')
  print(f'Загрузка GPU: {get_gpu_load()}')
