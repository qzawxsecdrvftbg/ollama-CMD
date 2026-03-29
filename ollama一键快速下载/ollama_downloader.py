#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ollama智能下载器 v4.8 - GitHub开源版

功能：
1. 动态阈值监控（可配置百分比）
2. 自动缓冲期（重启后/阈值更新后）
3. 智能重载（累计低速达阈值时重新检测MAX）
4. 全参数可通过config.txt配置
"""

import subprocess
import re
import time
import sys
import os
import threading
from datetime import datetime

# 获取脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.txt")


class ConfigManager:
    """配置管理器"""
    
    DEFAULT_CONFIG = {
        'is_ollama_installed': 0,
        'default_max_speed': 10.0,
        'threshold_percent': 65,
        'consecutive_low_threshold': 3,
        'total_low_threshold': 30,
        'threshold_update_auto_buffer': 5,
        'initial_warmup_seconds': 20,
        'restart_buffer_seconds': 19
    }
    
    @staticmethod
    def read_config():
        """读取配置文件"""
        config = ConfigManager.DEFAULT_CONFIG.copy()
        
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        if key in ['default_max_speed']:
                            config[key] = float(value)
                        elif key in ['is_ollama_installed', 'threshold_percent', 
                                    'consecutive_low_threshold', 'total_low_threshold', 
                                    'threshold_update_auto_buffer', 'initial_warmup_seconds',
                                    'restart_buffer_seconds']:
                            config[key] = int(float(value))
        except Exception as e:
            print(f"⚠️ 读取配置失败: {e}，使用默认值")
        
        return config
    
    @staticmethod
    def save_config(config):
        """保存配置文件"""
        try:
            config_text = f"""# Ollama智能下载器配置文件
# 修改后保存，下次运行生效

# Ollama安装状态（0=未安装，1=已安装，首次运行自动检测）
is_ollama_installed={config.get('is_ollama_installed', 0)}

# 默认预估最大网速（MB/s）
default_max_speed={config.get('default_max_speed', 10.0)}

# 速度阈值比例（%，低于此比例视为低速，默认65）
threshold_percent={config.get('threshold_percent', 65)}

# 连续低速阈值（达到此次数后重启下载）
consecutive_low_threshold={config.get('consecutive_low_threshold', 3)}

# 累计低速阈值（达到此次数后重新检测MAX值）
total_low_threshold={config.get('total_low_threshold', 30)}

# 阈值更新自动缓冲触发次数（达到此次数后自动开启缓冲期）
threshold_update_auto_buffer={config.get('threshold_update_auto_buffer', 5)}

# 初始预热期时长（秒，用于检测实际MAX值）
initial_warmup_seconds={config.get('initial_warmup_seconds', 20)}

# 重启/自动缓冲期时长（秒，此期间不检测低速）
restart_buffer_seconds={config.get('restart_buffer_seconds', 19)}
"""
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                f.write(config_text)
            return True
        except Exception as e:
            print(f"⚠️ 保存配置失败: {e}")
            return False
    
    @staticmethod
    def ensure_config_exists():
        """确保配置文件存在"""
        if not os.path.exists(CONFIG_FILE):
            print(f"📝 创建默认配置文件: {CONFIG_FILE}")
            ConfigManager.save_config(ConfigManager.DEFAULT_CONFIG)
            return ConfigManager.DEFAULT_CONFIG
        return ConfigManager.read_config()


class OllamaDownloader:
    def __init__(self, model_name, config):
        self.model_name = model_name
        self.config = config
        
        # 基础参数
        self.max_speed_estimate = config['default_max_speed']
        self.threshold_percent = config['threshold_percent']
        
        # 阈值参数
        self.consecutive_low_threshold = config['consecutive_low_threshold']
        self.total_low_threshold = config['total_low_threshold']
        self.threshold_update_auto_buffer = config['threshold_update_auto_buffer']
        
        # 时间参数
        self.initial_warmup_seconds = config['initial_warmup_seconds']
        self.restart_buffer_seconds = config['restart_buffer_seconds']
        
        # 动态计算值
        self.detected_max_speed = 0.0
        self.min_speed_threshold = 0.0
        
        # 状态
        self.current_speed = 0.0
        self.process = None
        self.consecutive_low_count = 0
        self.total_low_count = 0
        self.attempt = 0
        self.error_count = 0
        self.start_time = None
        
        # 模式
        self.is_first_warmup = True
        self.is_restart_buffer = False
        self.in_reload_mode = False
        
        # 阈值更新计数
        self.threshold_update_count = 0
        self.auto_buffer_triggered = False
        
    def format_time(self, seconds):
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            return f"{seconds/60:.1f}分钟"
        else:
            return f"{seconds/3600:.2f}小时"
        
    def print_header(self):
        print("=" * 70)
        print("          Ollama智能下载器 v4.8")
        print("=" * 70)
        print(f"模型：{self.model_name}")
        print(f"预估速度：{self.max_speed_estimate} MB/s")
        print(f"阈值比例：{self.threshold_percent}%")
        print(f"预热期：{self.initial_warmup_seconds}秒 | 缓冲期：{self.restart_buffer_seconds}秒")
        print(f"连续低速：{self.consecutive_low_threshold}次重启 | 累计低速：{self.total_low_threshold}次重载")
        print(f"自动缓冲：阈值更新{self.threshold_update_auto_buffer}次触发")
        print("=" * 70)
        print()
        
    def validate_model_name(self):
        if not self.model_name or len(self.model_name) < 2:
            print("❌ 错误：模型名称太短")
            return False
            
        if ':' not in self.model_name:
            print(f"⚠️ 警告：模型名称'{self.model_name}'没有指定标签")
            print("💡 常见标签：llama3:8b, qwen2.5:7b, qwen3:0.6b")
            response = input("是否自动添加':latest'标签？(y/n): ")
            if response.lower() == 'y':
                self.model_name += ":latest"
                print(f"✅ 已更新为：{self.model_name}")
            else:
                return False
        return True
        
    def parse_speed(self, line):
        match = re.search(r'(\d+\.?\d*)\s*MB/s', line)
        if match:
            return float(match.group(1))
        match = re.search(r'(\d+\.?\d*)\s*GB/s', line)
        if match:
            return float(match.group(1)) * 1024
        match = re.search(r'(\d+\.?\d*)\s*KB/s', line)
        if match:
            return float(match.group(1)) / 1024
        return None
        
    def check_model_exists(self):
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore',
                timeout=10
            )
            return self.model_name in result.stdout
        except:
            return False
            
    def read_output(self, pipe, output_list):
        try:
            for line in iter(pipe.readline, ''):
                if line:
                    line = line.strip()
                    output_list.append(line)
        except:
            pass
        finally:
            pipe.close()
            
    def check_error_in_output(self, output_lines):
        error_patterns = [
            'file does not exist',
            'pull model manifest',
            'error',
            'not found',
            'unauthorized',
            'connection refused',
            'invalid'
        ]
        
        for line in output_lines[-5:]:
            line_lower = line.lower()
            for pattern in error_patterns:
                if pattern in line_lower:
                    return line
        return None
    
    def calculate_threshold(self, max_speed):
        """根据配置的百分比计算阈值"""
        return max_speed * (self.threshold_percent / 100.0)
    
    def enter_reload_mode(self):
        print(f"\n\n🔄 累计{self.total_low_count}次低速（阈值{self.total_low_threshold}），进入重载模式...")
        print("🔍 重新检测最大网速...")
        self.in_reload_mode = True
        self.is_first_warmup = True
        self.is_restart_buffer = False
        self.detected_max_speed = 0.0
        self.min_speed_threshold = 0.0
        self.total_low_count = 0
        self.consecutive_low_count = 0
        self.threshold_update_count = 0
        self.auto_buffer_triggered = False
            
    def enter_auto_buffer(self):
        """进入自动缓冲期"""
        print(f"\n\n🎯 阈值已连续更新{self.threshold_update_count}次（达到{self.threshold_update_auto_buffer}次阈值）！")
        print(f"💤 自动开启缓冲期{self.restart_buffer_seconds}秒（不检测低速）...")
        self.is_restart_buffer = True
        self.auto_buffer_triggered = True
        self.threshold_update_count = 0
            
    def download_with_monitor(self):
        self.attempt += 1
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # 打印阶段标题
        if self.in_reload_mode:
            print(f"\n[{timestamp}] 🔄 重载模式：重新检测最大网速...")
            self.in_reload_mode = False
        elif self.is_restart_buffer:
            if self.auto_buffer_triggered:
                print(f"\n[{timestamp}] 🚀 第{self.attempt}次尝试（自动缓冲期{self.restart_buffer_seconds}秒）...")
            else:
                print(f"\n[{timestamp}] 🚀 第{self.attempt}次尝试（重启缓冲期{self.restart_buffer_seconds}秒）...")
        elif self.is_first_warmup and self.attempt == 1:
            print(f"\n[{timestamp}] 🚀 首次下载（预热期{self.initial_warmup_seconds}秒）...")
        else:
            print(f"\n[{timestamp}] 🚀 第{self.attempt}次尝试...")
            
        print("-" * 70)
        
        if self.attempt == 1:
            self.start_time = datetime.now()
        
        # 重置单次尝试的连续计数器
        self.consecutive_low_count = 0
        self.current_speed = 0.0
        
        # 启动进程
        try:
            self.process = subprocess.Popen(
                ["ollama", "pull", self.model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore',
                bufsize=1,
                universal_newlines=True
            )
        except Exception as e:
            print(f"\n❌ 启动失败：{e}")
            return False
            
        output_lines = []
        stdout_thread = threading.Thread(
            target=self.read_output,
            args=(self.process.stdout, output_lines)
        )
        stdout_thread.daemon = True
        stdout_thread.start()
        
        # 监控变量
        last_speed_time = time.time()
        start_time = time.time()
        last_display_update = 0
        
        # 设置阶段结束时间
        if self.is_first_warmup:
            phase_end = start_time + self.initial_warmup_seconds
        elif self.is_restart_buffer:
            phase_end = start_time + self.restart_buffer_seconds
        else:
            phase_end = None
        
        while self.process.poll() is None:
            current_time = time.time()
            
            # 每秒更新一次显示
            should_update_display = (current_time - last_display_update) >= 1.0
            
            # 检查错误
            error_line = self.check_error_in_output(output_lines)
            if error_line:
                print(f"\n❌ 检测到错误：{error_line}")
                self.error_count += 1
                if self.error_count >= 3:
                    print("\n⚠️ 多次出现错误，停止重试")
                    return "FATAL_ERROR"
                self.terminate_process()
                return False
            
            # 获取最新输出
            if output_lines:
                last_line = output_lines[-1]
                
                # 解析速度
                speed = self.parse_speed(last_line)
                
                if speed is not None and speed > 0:
                    self.current_speed = speed
                    last_speed_time = current_time
                    
                    # ===== 预热期 =====
                    if self.is_first_warmup:
                        if speed > self.detected_max_speed:
                            self.detected_max_speed = speed
                        
                        if should_update_display:
                            last_display_update = current_time
                            remaining = int(phase_end - current_time)
                            if remaining >= 0:
                                print(f"\r🔥 预热中... {remaining}秒 | 当前：{speed:.1f} MB/s | 最大：{self.detected_max_speed:.1f} MB/s    ", end='', flush=True)
                        
                        if current_time > phase_end:
                            self.min_speed_threshold = self.calculate_threshold(self.detected_max_speed)
                            print(f"\n\n✅ 预热期结束！")
                            print(f"📊 检测到的最大速度：{self.detected_max_speed:.1f} MB/s")
                            print(f"🎯 动态阈值已设定：{self.min_speed_threshold:.1f} MB/s（最大速度的{self.threshold_percent}%）")
                            print(f"💡 提示：阈值连续更新{self.threshold_update_auto_buffer}次将自动开启缓冲期")
                            print("-" * 70)
                            self.is_first_warmup = False
                            phase_end = None
                            last_display_update = 0
                            
                    # ===== 缓冲期 =====
                    elif self.is_restart_buffer:
                        if speed > self.detected_max_speed:
                            self.detected_max_speed = speed
                            self.min_speed_threshold = self.calculate_threshold(self.detected_max_speed)
                        
                        if should_update_display:
                            last_display_update = current_time
                            remaining = int(phase_end - current_time)
                            if remaining >= 0:
                                buffer_type = "自动缓冲" if self.auto_buffer_triggered else "重启缓冲"
                                print(f"\r💤 {buffer_type}... {remaining}秒 | 当前：{speed:.1f} MB/s | 最大：{self.detected_max_speed:.1f} MB/s    ", end='', flush=True)
                        
                        if current_time > phase_end:
                            print(f"\n\n✅ 缓冲期结束！继续监控...")
                            print(f"🎯 当前阈值：{self.min_speed_threshold:.1f} MB/s（最大速度的{self.threshold_percent}%）")
                            print("-" * 70)
                            self.is_restart_buffer = False
                            self.auto_buffer_triggered = False
                            phase_end = None
                            last_display_update = 0
                            
                    # ===== 正常监控期 =====
                    else:
                        # 发现更高速度，更新基准
                        if speed > self.detected_max_speed:
                            old_max = self.detected_max_speed
                            self.detected_max_speed = speed
                            self.min_speed_threshold = self.calculate_threshold(self.detected_max_speed)
                            self.threshold_update_count += 1
                            
                            print(f"\n📈 发现更高速度！更新基准：{old_max:.1f} → {self.detected_max_speed:.1f} MB/s")
                            print(f"🎯 新阈值：{self.min_speed_threshold:.1f} MB/s（第{self.threshold_update_count}/{self.threshold_update_auto_buffer}次更新）")
                            
                            if self.threshold_update_count >= self.threshold_update_auto_buffer:
                                self.enter_auto_buffer()
                                start_time = current_time
                                phase_end = start_time + self.restart_buffer_seconds
                                continue
                        
                        # 检查是否低于阈值
                        is_low = speed < self.min_speed_threshold
                        
                        if is_low:
                            self.consecutive_low_count += 1
                            self.total_low_count += 1
                            self.threshold_update_count = 0
                            
                            if self.total_low_count >= self.total_low_threshold:
                                print(f"\n⚠️ 累计{self.total_low_count}次低速！重新检测MAX...")
                                self.terminate_process()
                                self.enter_reload_mode()
                                return False
                            
                            if self.consecutive_low_count >= self.consecutive_low_threshold:
                                print(f"\n⚠️ 连续{self.consecutive_low_count}次低速（阈值{self.consecutive_low_threshold}）")
                                print(f"📉 基准最大：{self.detected_max_speed:.1f} MB/s | 累计低速：{self.total_low_count}/{self.total_low_threshold}")
                                print("🔄 正在重启下载...")
                                self.terminate_process()
                                self.is_restart_buffer = True
                                self.auto_buffer_triggered = False
                                return False
                            
                            status = f"⚠️ 低速{self.consecutive_low_count}/{self.consecutive_low_threshold}"
                        else:
                            if self.consecutive_low_count > 0:
                                self.consecutive_low_count = 0
                            status = "正常"
                        
                        if should_update_display:
                            last_display_update = current_time
                            status_line = (f"当前：{speed:.1f} MB/s | 阈值：{self.min_speed_threshold:.1f} MB/s | "
                                         f"状态：{status} | 累计：{self.total_low_count}/{self.total_low_threshold} | "
                                         f"更新：{self.threshold_update_count}/{self.threshold_update_auto_buffer}")
                            print(f"\r{status_line:<95}", end='', flush=True)
                        
                else:
                    if should_update_display and len(output_lines) > 0:
                        last_display_update = current_time
                        if '%' in last_line or 'pulling' in last_line:
                            print(f"\r{last_line[:80]:<80}", end='', flush=True)
                        
            # 卡死检测
            if not self.is_first_warmup and not self.is_restart_buffer:
                if current_time - last_speed_time > 180:
                    print("\n⏱️ 3分钟无响应，正在重启...")
                    self.terminate_process()
                    self.is_restart_buffer = True
                    self.auto_buffer_triggered = False
                    return False
                
            # 检查完成
            if self.check_model_exists():
                print(f"\n✅ 下载完成！")
                self.terminate_process()
                return True
            
            time.sleep(0.1)
                
        time.sleep(1)
        return self.check_model_exists()
        
    def terminate_process(self):
        if self.process:
            try:
                self.process.terminate()
                time.sleep(0.5)
                if self.process.poll() is None:
                    self.process.kill()
                    time.sleep(0.5)
            except:
                pass
            self.process = None
            
    def run(self):
        self.print_header()
        
        if not self.validate_model_name():
            return False
        
        print(f"🔍 检查模型 {self.model_name} 是否已存在...")
        if self.check_model_exists():
            print(f"✅ 模型已存在，无需下载！")
            return True
            
        print("💡 工作原理：")
        print(f"   • {self.initial_warmup_seconds}秒预热检测MAX值")
        print(f"   • 动态阈值 = MAX值 × {self.threshold_percent}%")
        print(f"   • 每次重启{self.restart_buffer_seconds}秒缓冲（不检测低速）")
        print(f"   • 连续{self.consecutive_low_threshold}次低速→重启")
        print(f"   • 累计{self.total_low_threshold}次低速→重载MAX")
        print(f"   • 阈值更新{self.threshold_update_auto_buffer}次→自动缓冲")
        print()
        
        # 直接开始下载
        print(f"🚀 开始下载！预估：{self.max_speed_estimate} MB/s | 阈值比例：{self.threshold_percent}%")
        print("=" * 70)
            
        # 下载循环
        success = False
        while not success:
            result = self.download_with_monitor()
            if result == "FATAL_ERROR":
                print("\n❌ 下载失败")
                return False
            if not result:
                print("⏳ 等待2秒后重试...")
                time.sleep(2)
            else:
                success = True
                
        if self.start_time:
            total_time = (datetime.now() - self.start_time).total_seconds()
            print("\n" + "=" * 70)
            print(f"🎉 {self.model_name} 下载完成！")
            print(f"⏱️ 总用时：{self.format_time(total_time)}")
            print(f"📊 最终MAX：{self.detected_max_speed:.1f} MB/s | 阈值：{self.min_speed_threshold:.1f} MB/s（{self.threshold_percent}%）")
            print(f"📈 累计低速：{self.total_low_count}次")
            print("=" * 70)
        return True


def main():
    try:
        # 确保配置文件存在并读取
        config = ConfigManager.ensure_config_exists()
        
        if len(sys.argv) >= 2:
            model_name = sys.argv[1]
        else:
            model_name = input("请输入模型名称（如：qwen3:0.6b）：").strip()
            if not model_name:
                model_name = "qwen3:0.6b"
        
        downloader = OllamaDownloader(model_name, config)
        success = downloader.run()
        
        if not success:
            print("\n💡 可用模型：llama3:8b, qwen2.5:7b, qwen3:0.6b等")
            
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    input("\n按回车键退出...")


if __name__ == "__main__":
    main()
