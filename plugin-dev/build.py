"""Compatibility entry point for plugins under plugin-dev."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('nexus_plugin_sdk_build', ROOT.parent / 'nexus-plugin-sdk' / 'build.py')
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


def build(plugin):
    source = (ROOT / plugin).resolve()
    if source.parent != ROOT:
        raise ValueError('请选择 plugin-dev 下的一个插件目录')
    return sdk.build(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plugin', help='插件子目录名，例如 qq-bridge')
    print(build(parser.parse_args().plugin))
