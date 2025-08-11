# -*- coding: utf-8 -*-
import sys
import os
import json
import time
from datetime import datetime
from PyQt5.QtCore import Qt, QRunnable, QThreadPool, pyqtSignal, QObject, QTimer
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QTextEdit, QFileDialog,
    QMessageBox, QInputDialog, QLabel, QGroupBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QLineEdit,
    QAbstractItemView, QFormLayout, QDialog, QTabWidget, QTextBrowser,
    QMenu, QAction, QCheckBox, QProgressBar, QDialogButtonBox
)
from PyQt5.QtGui import QColor, QFont
from git import Repo, InvalidGitRepositoryError, GitCommandError, RemoteProgress

CONFIG_FILE = "git_repos.json"


class GitProgressHandler(RemoteProgress):
    """Git操作进度处理类"""

    def __init__(self, signals):
        super().__init__()
        self.signals = signals
        self.last_update = 0

    def update(self, op_code, cur_count, max_count=None, message=''):
        # 限制进度更新频率，避免过于频繁的UI更新
        current_time = time.time()
        if current_time - self.last_update > 0.5 or message:
            # 发送进度信息
            if message:
                self.signals.progress.emit(message)
            elif max_count and max_count > 0:
                percentage = (cur_count / max_count) * 100
                self.signals.progress.emit(f"进度: {percentage:.1f}%")
            else:
                self.signals.progress.emit("处理中...")
            self.last_update = current_time


class GitWorkerSignals(QObject):
    """工作线程信号类"""
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)


class GitWorker(QRunnable):
    """Git操作工作线程"""

    def __init__(self, repo_path, operation, *args):
        super().__init__()
        self.repo_path = repo_path
        self.operation = operation
        self.args = args
        self.signals = GitWorkerSignals()

    def run(self):
        start_time = time.time()
        try:
            # 记录Git仓库初始化时间
            repo_init_start = time.time()
            repo = Repo(self.repo_path)
            repo_init_time = time.time() - repo_init_start
            print(f"Git repository initialization took {repo_init_time:.2f} seconds")

            operation_start = time.time()

            if self.operation == "status":
                status_result = []
                git_status = repo.git.status('--porcelain')
                for line in git_status.split('\n'):
                    if line.strip():
                        if line.startswith("??"):
                            status_result.append(("未跟踪", line[3:], "➕"))
                        else:
                            code = line[0]
                            file_path = line[3:]
                            if code == "A":
                                status_result.append(("已暂存", file_path, "✅"))
                            elif code == "M":
                                status_result.append(("修改", file_path, "📝"))
                            elif code == "D":
                                status_result.append(("删除", file_path, "❌"))
                            else:
                                status_result.append(("未暂存", file_path, "📝"))
                self.signals.result.emit(status_result)

            elif self.operation == "log":
                commits = list(repo.iter_commits(max_count=20))
                log_result = []
                for commit in commits:
                    log_result.append({
                        'hash': commit.hexsha[:7],
                        'message': commit.summary,
                        'author': commit.author.name,
                        'date': commit.committed_datetime.strftime("%Y-%m-%d %H:%M"),
                        'full_hash': commit.hexsha
                    })
                self.signals.result.emit(log_result)

            elif self.operation == "branches":
                branch_info = {
                    'local_branches': [],
                    'remote_branches': [],
                    'active_branch': None
                }

                # 获取本地分支
                branch_info['local_branches'] = [branch.name for branch in repo.branches]
                # 获取当前活跃分支
                branch_info['active_branch'] = repo.active_branch.name

                # 获取远程分支
                try:
                    # 获取所有远程分支
                    for remote in repo.remotes:
                        for ref in remote.refs:
                            # 只添加远程分支，不包括 HEAD 引用
                            if not ref.name.endswith('/HEAD'):
                                # 提取分支名，去掉 "origin/" 前缀
                                remote_name = ref.name
                                if '/' in remote_name and not remote_name.startswith('origin/HEAD'):
                                    branch_info['remote_branches'].append(remote_name)
                except Exception as e:
                    print(f"获取远程分支时出错: {e}")
                    pass

                self.signals.result.emit(branch_info)

            elif self.operation == "add":
                repo.git.add('.')
                self.signals.result.emit("所有文件已暂存")

            elif self.operation == "commit":
                message = self.args[0] if self.args else "Update"
                repo.git.commit('-m', message)
                self.signals.result.emit(f"提交成功: {message}")

            elif self.operation == "push":
                # 设置Git配置优化
                repo.git.config('http.lowSpeedLimit', '1000')
                repo.git.config('http.lowSpeedTime', '30')

                origin = repo.remote(name='origin')
                progress_handler = GitProgressHandler(self.signals)

                try:
                    push_start = time.time()
                    origin.push(progress=progress_handler)
                    push_time = time.time() - push_start
                    print(f"Push operation took {push_time:.2f} seconds")
                    self.signals.result.emit("推送成功")
                except GitCommandError as e:
                    # 检查是否是因为没有设置上游分支
                    if "has no upstream branch" in str(e):
                        # 获取当前分支名
                        current_branch = repo.active_branch.name
                        # 提示用户设置上游分支
                        error_msg = (f"分支 '{current_branch}' 没有设置上游分支。\n"
                                     f"请使用 'git push --set-upstream origin {current_branch}' "
                                     f"命令设置上游分支，或者在界面中选择相应选项。")
                        self.signals.error.emit(error_msg)
                    else:
                        # 其他推送错误
                        self.signals.error.emit(str(e))

            elif self.operation == "push_with_upstream":
                # 设置Git配置优化
                repo.git.config('http.lowSpeedLimit', '1000')
                repo.git.config('http.lowSpeedTime', '30')

                # 推送并设置上游分支
                origin = repo.remote(name='origin')
                progress_handler = GitProgressHandler(self.signals)
                current_branch = repo.active_branch.name

                try:
                    # 修复：使用正确的 --set-upstream 选项而不是 --setUpstream
                    origin.push(refspec=f"{current_branch}:{current_branch}",
                                set_upstream=True,  # 修正参数名
                                progress=progress_handler)
                    self.signals.result.emit(f"推送成功，并已设置上游分支为 origin/{current_branch}")
                except GitCommandError as e:
                    self.signals.error.emit(str(e))

            elif self.operation == "pull":
                # 设置Git配置优化
                repo.git.config('http.lowSpeedLimit', '1000')
                repo.git.config('http.lowSpeedTime', '30')

                origin = repo.remote(name='origin')

                # 获取拉取选项参数
                rebase = self.args[0] if len(self.args) > 0 else True
                prune = self.args[1] if len(self.args) > 1 else True

                # 构建拉取参数
                pull_kwargs = {'rebase': rebase}
                if prune:
                    pull_kwargs['prune'] = prune

                # 添加进度处理
                progress_handler = GitProgressHandler(self.signals)
                pull_kwargs['progress'] = progress_handler

                pull_start = time.time()
                origin.pull(**pull_kwargs)
                pull_time = time.time() - pull_start
                print(f"Pull operation took {pull_time:.2f} seconds")
                self.signals.result.emit("拉取成功")

            elif self.operation == "checkout":
                if len(self.args) == 3 and self.args[0] == "-b":
                    # 创建并切换到新分支（用于跟踪远程分支）
                    branch_name = self.args[1]
                    remote_branch = self.args[2]
                    repo.git.checkout('-b', branch_name, remote_branch)
                    self.signals.result.emit(f"创建并切换到新分支: {branch_name} (跟踪 {remote_branch})")
                else:
                    branch_name = self.args[0]
                    repo.git.checkout(branch_name)
                    self.signals.result.emit(f"切换到分支: {branch_name}")

            elif self.operation == "create_branch":
                branch_name = self.args[0]
                repo.git.checkout('-b', branch_name)
                self.signals.result.emit(f"创建并切换到新分支: {branch_name}")

            elif self.operation == "merge":
                branch_name = self.args[0]
                repo.git.merge(branch_name)
                self.signals.result.emit(f"成功合并分支: {branch_name}")

            elif self.operation == "cherry_pick":
                commit_hash = self.args[0]
                repo.git.cherry_pick(commit_hash)
                self.signals.result.emit(f"成功cherry-pick提交: {commit_hash[:7]}")

            elif self.operation == "show_commit":
                commit_hash = self.args[0]
                commit = repo.commit(commit_hash)

                # 获取提交的文件变更
                diff = commit.diff(commit.parents[0]) if commit.parents else commit.diff()  # 初始提交

                files_changed = []
                for diff_item in diff:
                    try:
                        # 获取文件差异
                        if commit.parents:
                            diff_content = repo.git.diff(commit.parents[0].hexsha, commit.hexsha,
                                                         '--', diff_item.b_path or diff_item.a_path)
                        else:
                            # 对于初始提交，显示文件完整内容
                            diff_content = repo.git.show(f"{commit.hexsha}:{diff_item.b_path or diff_item.a_path}")

                        file_info = {
                            'path': diff_item.b_path or diff_item.a_path,
                            'change_type': diff_item.change_type,
                            'diff': diff_content
                        }
                        files_changed.append(file_info)
                    except Exception as e:
                        # 如果获取差异失败，至少显示文件路径和变更类型
                        file_info = {
                            'path': diff_item.b_path or diff_item.a_path,
                            'change_type': diff_item.change_type,
                            'diff': f"无法获取差异信息: {str(e)}"
                        }
                        files_changed.append(file_info)

                result = {
                    'commit': {
                        'hash': commit.hexsha[:7],
                        'full_hash': commit.hexsha,
                        'message': commit.summary,
                        'author': commit.author.name,
                        'date': commit.committed_datetime.strftime("%Y-%m-%d %H:%M")
                    },
                    'files_changed': files_changed
                }
                self.signals.result.emit(result)

            elif self.operation == "diff":
                file_path = self.args[0]
                diff_content = repo.git.diff('HEAD', '--', file_path)
                self.signals.result.emit(diff_content)

            elif self.operation == "checkout_file":
                file_path = self.args[0]
                repo.git.checkout('--', file_path)
                self.signals.result.emit(f"已取消变更: {file_path}")

            elif self.operation == "add_remote":
                remote_name = self.args[0]
                remote_url = self.args[1]
                repo.create_remote(remote_name, remote_url)
                self.signals.result.emit(f"远程仓库 '{remote_name}' 已添加，URL: {remote_url}")

            elif self.operation == "delete_branch":
                branch_name = self.args[0]
                force = self.args[1] if len(self.args) > 1 else False
                if force:
                    repo.git.branch('-D', branch_name)
                    self.signals.result.emit(f"分支 '{branch_name}' 已强制删除")
                else:
                    repo.git.branch('-d', branch_name)
                    self.signals.result.emit(f"分支 '{branch_name}' 已删除")

            operation_time = time.time() - operation_start
            total_time = time.time() - start_time
            print(f"Git operation '{self.operation}' took {operation_time:.2f} seconds")
            print(f"Total worker execution time: {total_time:.2f} seconds")

        except Exception as e:
            self.signals.error.emit(str(e))


class CloneDialog(QDialog):
    """克隆仓库对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.repo_url = None
        self.local_path = None
        self.init_ui()
        self.setWindowTitle("克隆远程仓库")
        self.resize(500, 200)

    def init_ui(self):
        layout = QVBoxLayout()

        # 仓库URL输入
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("仓库URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("例如: https://github.com/user/repo.git")
        url_layout.addWidget(self.url_input)
        layout.addLayout(url_layout)

        # 本地路径选择
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("本地路径:"))
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("选择本地目录存放克隆的仓库")
        path_layout.addWidget(self.path_input)
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self.browse_path)
        path_layout.addWidget(self.browse_btn)
        layout.addLayout(path_layout)

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.clone_btn = QPushButton("克隆")
        self.clone_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.clone_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def browse_path(self):
        """浏览本地路径"""
        directory = QFileDialog.getExistingDirectory(self, "选择本地目录")
        if directory:
            self.path_input.setText(directory)

    def accept(self):
        """确认克隆"""
        self.repo_url = self.url_input.text().strip()
        self.local_path = self.path_input.text().strip()

        if not self.repo_url:
            QMessageBox.warning(self, "警告", "请输入仓库URL")
            return

        if not self.local_path:
            QMessageBox.warning(self, "警告", "请选择本地路径")
            return

        # 检查本地路径是否存在
        if not os.path.exists(self.local_path):
            QMessageBox.warning(self, "警告", "本地路径不存在")
            return

        # 获取仓库名称
        repo_name = self.repo_url.split('/')[-1].replace('.git', '')
        self.clone_target_path = os.path.join(self.local_path, repo_name)

        # 检查目标路径是否已存在
        if os.path.exists(self.clone_target_path):
            reply = QMessageBox.question(
                self, "确认",
                f"目录 {self.clone_target_path} 已存在，是否继续克隆？",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        super().accept()


class DiffDialog(QDialog):
    """差异显示对话框"""

    def __init__(self, file_path, diff_content, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.diff_content = diff_content
        self.init_ui()
        self.setWindowTitle(f"文件变更: {file_path}")
        self.resize(800, 600)

    def init_ui(self):
        layout = QVBoxLayout()

        # 文件路径标签
        path_label = QLabel(f"文件: {self.file_path}")
        path_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(path_label)

        # 差异内容显示
        self.diff_text = QTextBrowser()
        self.diff_text.setFont(QFont("Courier New", 10))

        # 简单的语法高亮
        formatted_diff = self.format_diff_content(self.diff_content)
        self.diff_text.setHtml(formatted_diff)

        layout.addWidget(self.diff_text)

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def format_diff_content(self, content):
        """简单格式化差异内容"""
        lines = content.split('\n')
        formatted_lines = []

        for line in lines:
            if line.startswith('+') and not line.startswith('+++'):
                formatted_lines.append(f'<span style="color: green; background-color: #f0fff0;">{line}</span>')
            elif line.startswith('-') and not line.startswith('---'):
                formatted_lines.append(f'<span style="color: red; background-color: #fff0f0;">{line}</span>')
            elif line.startswith('@'):
                formatted_lines.append(f'<span style="color: blue; background-color: #f0f0ff;">{line}</span>')
            else:
                formatted_lines.append(line)

        return '<br>'.join(formatted_lines)


class CommitDetailDialog(QDialog):
    """提交详情对话框"""

    def __init__(self, commit_data, parent=None):
        super().__init__(parent)
        self.commit_data = commit_data
        self.init_ui()
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setWindowTitle("提交详情")
        self.resize(800, 600)

    def init_ui(self):
        layout = QVBoxLayout()

        # 标题区域
        title_group = QGroupBox("提交信息")
        title_layout = QVBoxLayout()
        title_label = QLabel(f"<h3>{self.commit_data['commit']['message']}</h3>")
        title_label.setWordWrap(True)
        title_layout.addWidget(title_label)
        title_group.setLayout(title_layout)
        layout.addWidget(title_group)

        # 详细信息区域
        detail_group = QGroupBox("详细信息")
        detail_layout = QFormLayout()

        hash_label = QLabel(self.commit_data['commit']['full_hash'])
        hash_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addRow(QLabel("提交哈希:"), hash_label)

        author_label = QLabel(self.commit_data['commit']['author'])
        detail_layout.addRow(QLabel("作者:"), author_label)

        date_label = QLabel(self.commit_data['commit']['date'])
        detail_layout.addRow(QLabel("日期:"), date_label)

        detail_group.setLayout(detail_layout)
        layout.addWidget(detail_group)

        # 文件变更区域
        files_group = QGroupBox("文件变更")
        files_layout = QVBoxLayout()

        self.files_tabs = QTabWidget()
        files_layout.addWidget(self.files_tabs)

        # 添加文件变更标签页
        for file_info in self.commit_data['files_changed']:
            self.add_file_tab(file_info)

        files_group.setLayout(files_layout)
        layout.addWidget(files_group)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def add_file_tab(self, file_info):
        """添加文件变更标签页"""
        tab = QWidget()
        tab_layout = QVBoxLayout()

        # 文件路径和变更类型
        header_layout = QHBoxLayout()
        file_path_label = QLabel(f"<b>{file_info['path']}</b>")
        change_type_label = QLabel(f"({file_info['change_type']})")
        header_layout.addWidget(file_path_label)
        header_layout.addWidget(change_type_label)
        header_layout.addStretch()
        tab_layout.addLayout(header_layout)

        # 差异内容
        diff_text = QTextBrowser()
        diff_text.setFont(QFont("Courier New", 10))
        diff_text.setPlainText(file_info['diff'] if file_info['diff'] else "无差异信息")
        tab_layout.addWidget(diff_text)

        tab.setLayout(tab_layout)

        # 添加标签页
        tab_title = os.path.basename(file_info['path']) if file_info['path'] else "未知文件"
        self.files_tabs.addTab(tab, tab_title)


class CloneWorker(QRunnable):
    """克隆仓库工作线程"""

    def __init__(self, repo_url, local_path, clone_target_path):
        super().__init__()
        self.repo_url = repo_url
        self.local_path = local_path
        self.clone_target_path = clone_target_path
        self.signals = GitWorkerSignals()

    def run(self):
        try:
            # 使用GitPython克隆仓库
            progress_handler = GitProgressHandler(self.signals)
            repo = Repo.clone_from(self.repo_url, self.clone_target_path, progress=progress_handler)
            self.signals.result.emit(f"仓库克隆成功: {self.clone_target_path}")
        except Exception as e:
            self.signals.error.emit(str(e))


class GitManager(QWidget):
    def __init__(self):
        super().__init__()
        self.repo_paths = []
        self.current_repo = None
        self.current_repo_path = None
        self.threadpool = QThreadPool()
        # 设置线程池最大线程数
        self.threadpool.setMaxThreadCount(5)
        self.active_operations = set()  # 跟踪正在进行的操作
        self.init_ui()
        self.load_config()
        self.setWindowTitle("多仓库Git管理系统")
        self.resize(1200, 800)

    def init_ui(self):
        # 主布局
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # 顶部按钮区域
        # 顶部按钮区域 - 修改此处，将添加远程仓库按钮移到这里
        top_layout = QHBoxLayout()
        self.add_repo_btn = QPushButton("添加仓库")
        self.add_repo_btn.clicked.connect(self.add_repo)
        self.remove_repo_btn = QPushButton("移除仓库")
        self.remove_repo_btn.clicked.connect(self.remove_repo)
        self.clone_repo_btn = QPushButton("克隆仓库")  # 新增按钮
        self.clone_repo_btn.clicked.connect(self.clone_repo)  # 连接点击事件
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh_current_repo)

        top_layout.addWidget(self.add_repo_btn)
        top_layout.addWidget(self.remove_repo_btn)
        top_layout.addWidget(self.clone_repo_btn)  # 添加到顶部按钮区域
        top_layout.addWidget(self.refresh_btn)
        top_layout.addStretch()
        main_layout.addLayout(top_layout)

        # 分割器
        splitter = QSplitter(Qt.Horizontal)

        # 左侧仓库列表
        left_panel = QGroupBox("仓库列表")
        left_layout = QVBoxLayout()

        self.repo_list = QListWidget()
        self.repo_list.setSelectionMode(QAbstractItemView.ExtendedSelection)  # 启用多选
        self.repo_list.currentItemChanged.connect(self.on_repo_selected)
        left_layout.addWidget(self.repo_list)
        left_panel.setLayout(left_layout)
        splitter.addWidget(left_panel)

        # 右侧功能区
        right_panel = QGroupBox("仓库操作")
        right_layout = QVBoxLayout()

        # 仓库信息
        info_layout = QHBoxLayout()
        self.repo_info_label = QLabel("请选择一个仓库")
        info_layout.addWidget(self.repo_info_label)
        info_layout.addStretch()
        right_layout.addLayout(info_layout)

        # 分支操作
        branch_group = QGroupBox("分支管理")
        branch_layout = QVBoxLayout()

        # 分支切换和创建
        branch_control_layout = QHBoxLayout()
        branch_control_layout.addWidget(QLabel("当前分支:"))
        self.branch_combo = QComboBox()
        self.branch_combo.activated[int].connect(self.on_branch_activated)
        self.branch_combo.setFixedWidth(300)
        self.branch_combo.setMaxVisibleItems(30)
        # 确保滚动条正常显示
        branch_control_layout.addWidget(self.branch_combo)

        self.new_branch_input = QLineEdit()
        self.new_branch_input.setPlaceholderText("新分支名")
        branch_control_layout.addWidget(self.new_branch_input)

        self.create_branch_btn = QPushButton("创建分支")
        self.create_branch_btn.clicked.connect(self.create_branch)
        branch_control_layout.addWidget(self.create_branch_btn)
        branch_layout.addLayout(branch_control_layout)

        # 分支合并和远程操作 - 修改此处，移除添加远程仓库按钮
        branch_remote_layout = QHBoxLayout()

        # 合并分支
        branch_remote_layout.addWidget(QLabel("合并分支:"))
        self.merge_combo = QComboBox()
        branch_remote_layout.addWidget(self.merge_combo)

        self.merge_btn = QPushButton("合并到当前分支")
        self.merge_btn.clicked.connect(self.merge_branch)
        branch_remote_layout.addWidget(self.merge_btn)

        # 删除分支按钮
        self.delete_branch_btn = QPushButton("删除分支")
        self.delete_branch_btn.clicked.connect(self.delete_branch)
        branch_remote_layout.addWidget(self.delete_branch_btn)

        # 移除了 add_remote_btn 相关代码
        branch_layout.addLayout(branch_remote_layout)

        branch_group.setLayout(branch_layout)
        right_layout.addWidget(branch_group)

        # 文件状态
        status_group = QGroupBox("文件状态")
        status_layout = QVBoxLayout()
        self.status_table = QTableWidget(0, 3)
        self.status_table.setHorizontalHeaderLabels(["状态", "文件", "图标"])
        self.status_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.status_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # 添加双击和右键菜单事件
        self.status_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.status_table.customContextMenuRequested.connect(self.show_status_context_menu)
        self.status_table.itemDoubleClicked.connect(self.show_file_diff)
        status_layout.addWidget(self.status_table)

        status_btn_layout = QHBoxLayout()
        self.refresh_status_btn = QPushButton("刷新状态")
        self.refresh_status_btn.clicked.connect(self.refresh_status)
        self.stage_all_btn = QPushButton("暂存所有")
        self.stage_all_btn.clicked.connect(self.stage_all)
        status_btn_layout.addWidget(self.refresh_status_btn)
        status_btn_layout.addWidget(self.stage_all_btn)
        status_btn_layout.addStretch()
        status_layout.addLayout(status_btn_layout)

        status_group.setLayout(status_layout)
        right_layout.addWidget(status_group)

        # 提交操作
        commit_group = QGroupBox("提交")
        commit_layout = QVBoxLayout()
        self.commit_message = QTextEdit()
        self.commit_message.setPlaceholderText("输入提交信息...")
        self.commit_message.setMaximumHeight(60)
        commit_layout.addWidget(self.commit_message)

        commit_btn_layout = QHBoxLayout()
        self.commit_btn = QPushButton("提交")
        self.commit_btn.clicked.connect(self.commit)
        self.commit_push_btn = QPushButton("提交并推送")
        self.commit_push_btn.clicked.connect(self.commit_and_push)
        commit_btn_layout.addWidget(self.commit_btn)
        commit_btn_layout.addWidget(self.commit_push_btn)
        commit_btn_layout.addStretch()
        commit_layout.addLayout(commit_btn_layout)

        commit_group.setLayout(commit_layout)
        right_layout.addWidget(commit_group)

        # 远程操作
        remote_group = QGroupBox("远程操作")
        remote_layout = QVBoxLayout()

        # 拉取选项
        pull_options_layout = QHBoxLayout()
        pull_options_layout.addWidget(QLabel("拉取选项:"))
        self.pull_rebase_checkbox = QCheckBox("使用 rebase")
        self.pull_rebase_checkbox.setChecked(True)
        self.pull_prune_checkbox = QCheckBox("清理已删除的远程分支")
        self.pull_prune_checkbox.setChecked(True)
        pull_options_layout.addWidget(self.pull_rebase_checkbox)
        pull_options_layout.addWidget(self.pull_prune_checkbox)
        pull_options_layout.addStretch()
        remote_layout.addLayout(pull_options_layout)

        # 按钮
        remote_btn_layout = QHBoxLayout()
        self.pull_btn = QPushButton("拉取")
        self.pull_btn.clicked.connect(self.pull)
        self.push_btn = QPushButton("推送")
        self.push_btn.clicked.connect(self.push)
        self.push_set_upstream_btn = QPushButton("推送并设置上游")
        self.push_set_upstream_btn.clicked.connect(lambda: self.push(set_upstream=True))
        remote_btn_layout.addWidget(self.pull_btn)
        remote_btn_layout.addWidget(self.push_btn)
        remote_btn_layout.addWidget(self.push_set_upstream_btn)
        remote_btn_layout.addStretch()
        remote_layout.addLayout(remote_btn_layout)

        remote_group.setLayout(remote_layout)
        right_layout.addWidget(remote_group)

        # 历史提交
        history_group = QGroupBox("历史提交")
        history_layout = QVBoxLayout()
        self.history_list = QListWidget()
        self.history_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self.show_history_context_menu)
        self.history_list.itemDoubleClicked.connect(self.show_commit_detail)
        history_layout.addWidget(self.history_list)
        history_group.setLayout(history_layout)
        right_layout.addWidget(history_group)

        # 输出日志
        log_group = QGroupBox("输出日志")
        log_layout = QVBoxLayout()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(80)
        log_layout.addWidget(self.log_output)
        log_group.setLayout(log_layout)
        right_layout.addWidget(log_group)

        right_panel.setLayout(right_layout)
        splitter.addWidget(right_panel)

        splitter.setSizes([300, 900])
        main_layout.addWidget(splitter)

        # 初始化按钮状态
        self.update_button_states()

    def log_message(self, message):
        """记录日志信息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{timestamp}] {message}")
        self.log_output.moveCursor(self.log_output.textCursor().End)

    def set_button_loading(self, button, loading=True):
        """设置按钮加载状态"""
        if loading:
            button.setEnabled(False)
            button.setText(button.text() + " 中...")
        else:
            original_text = button.text().replace(" 中...", "")
            button.setText(original_text)
            button.setEnabled(True)

    def set_combo_loading(self, combo, loading=True):
        """设置组合框加载状态"""
        if loading:
            combo.setEnabled(False)
        else:
            combo.setEnabled(True)

    def update_button_states(self):
        """更新按钮状态"""
        has_repo = self.current_repo_path is not None

        # 远程操作按钮状态
        self.pull_btn.setEnabled(has_repo)
        self.push_btn.setEnabled(has_repo)
        self.push_set_upstream_btn.setEnabled(has_repo)

        # 其他依赖仓库的按钮
        self.refresh_btn.setEnabled(has_repo)
        self.refresh_status_btn.setEnabled(has_repo)
        self.stage_all_btn.setEnabled(has_repo)
        self.commit_btn.setEnabled(has_repo)
        self.commit_push_btn.setEnabled(has_repo)
        self.branch_combo.setEnabled(has_repo)
        self.new_branch_input.setEnabled(has_repo)
        self.create_branch_btn.setEnabled(has_repo)
        self.merge_combo.setEnabled(has_repo)
        self.merge_btn.setEnabled(has_repo)
        self.clone_repo_btn.setEnabled(True)  # 克隆按钮始终可用
        self.delete_branch_btn.setEnabled(has_repo)

    def load_config(self):
        """加载仓库配置"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.repo_paths = json.load(f)
                self.update_repo_list()
            except Exception as e:
                self.log_message(f"加载配置失败: {str(e)}")
        else:
            self.repo_paths = []

    def save_config(self):
        """保存仓库配置"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.repo_paths, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log_message(f"保存配置失败: {str(e)}")

    def update_repo_list(self):
        """更新仓库列表显示"""
        self.repo_list.clear()
        for path in self.repo_paths:
            if os.path.exists(path):
                item = QListWidgetItem(os.path.basename(path))
                item.setData(Qt.UserRole, path)
                item.setToolTip(path)
                self.repo_list.addItem(item)

    def add_repo(self):
        """添加仓库（支持多选）"""
        if "add_repo" in self.active_operations:
            return

        self.active_operations.add("add_repo")
        self.set_button_loading(self.add_repo_btn, True)

        # 使用单选模式避免Windows系统上的崩溃问题
        directory = QFileDialog.getExistingDirectory(self, "选择Git仓库目录")
        if directory:
            try:
                # 检查是否为有效的Git仓库
                repo = Repo(directory)
                repo_path = repo.working_dir

                # 检查是否已存在
                if repo_path not in self.repo_paths:
                    self.repo_paths.append(repo_path)
                    self.save_config()
                    self.update_repo_list()
                    self.log_message(f"已添加仓库: {os.path.basename(repo_path)}")
                else:
                    self.log_message(f"仓库已存在: {os.path.basename(repo_path)}")
            except InvalidGitRepositoryError:
                QMessageBox.warning(self, "错误", f"选择的目录不是有效的Git仓库: {directory}")
            except Exception as e:
                self.log_message(f"添加仓库时出错: {str(e)}")

        self.active_operations.discard("add_repo")
        self.set_button_loading(self.add_repo_btn, False)

    def add_multiple_repos(self):
        """添加多个仓库（备用方法）"""
        # 如果需要实现真正的多选功能，可以创建一个自定义对话框
        self.log_message("批量添加功能暂未实现，请逐个添加仓库")

    def remove_repo(self):
        """移除仓库"""
        if "remove_repo" in self.active_operations:
            return

        selected_items = self.repo_list.selectedItems()
        if not selected_items:
            self.log_message("请先选择要移除的仓库")
            return

        self.active_operations.add("remove_repo")
        self.set_button_loading(self.remove_repo_btn, True)

        repo_paths_to_remove = [item.data(Qt.UserRole) for item in selected_items]

        reply = QMessageBox.question(
            self, "确认",
            f"确定要移除选中的 {len(repo_paths_to_remove)} 个仓库吗？\n(仅从列表中移除，不会删除实际文件)",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            removed_repos = []
            for repo_path in repo_paths_to_remove:
                if repo_path in self.repo_paths:
                    self.repo_paths.remove(repo_path)
                    removed_repos.append(repo_path)

                    # 如果当前选中的是被删除的仓库，清除当前仓库信息
                    if self.current_repo_path == repo_path:
                        self.current_repo = None
                        self.current_repo_path = None
                        self.repo_info_label.setText("请选择一个仓库")
                        self.clear_repo_info()

            self.save_config()
            self.update_repo_list()
            self.log_message(
                f"已移除 {len(removed_repos)} 个仓库: {', '.join([os.path.basename(p) for p in removed_repos])}")

        self.active_operations.discard("remove_repo")
        self.set_button_loading(self.remove_repo_btn, False)

    def on_repo_selected(self, current, previous):
        """仓库选择事件"""
        if current:
            repo_path = current.data(Qt.UserRole)
            if os.path.exists(repo_path):
                try:
                    self.current_repo = Repo(repo_path)
                    self.current_repo_path = repo_path
                    self.repo_info_label.setText(f"当前仓库: {os.path.basename(repo_path)}")
                    self.refresh_current_repo()
                    self.log_message(f"已选择仓库: {repo_path}")
                except Exception as e:
                    self.log_message(f"打开仓库失败: {str(e)}")
            else:
                self.log_message("仓库路径不存在")
        else:
            self.current_repo = None
            self.current_repo_path = None
            self.repo_info_label.setText("请选择一个仓库")
            self.clear_repo_info()

        # 更新按钮状态
        self.update_button_states()

    def clear_repo_info(self):
        """清空仓库信息显示"""
        self.branch_combo.clear()
        self.merge_combo.clear()
        self.status_table.setRowCount(0)
        self.history_list.clear()
        self.commit_message.clear()

    def refresh_current_repo(self):
        """刷新当前仓库信息"""
        if "refresh" in self.active_operations:
            return

        if self.current_repo and self.current_repo_path:
            self.active_operations.add("refresh")
            self.set_button_loading(self.refresh_btn, True)

            self.refresh_branches()
            self.refresh_status()
            self.refresh_history()

            self.active_operations.discard("refresh")
            self.set_button_loading(self.refresh_btn, False)

    def execute_git_task(self, operation, *args, callback=None):
        """执行Git任务"""
        if not self.current_repo_path:
            self.log_message("请先选择一个仓库")
            return

        # 立即提供反馈
        self.log_message(f"准备执行 {operation} 操作...")

        worker = GitWorker(self.current_repo_path, operation, *args)
        if callback:
            worker.signals.result.connect(callback)
        worker.signals.error.connect(self.handle_git_error)
        worker.signals.progress.connect(self.log_message)
        self.threadpool.start(worker)
        # 强制处理事件以立即显示日志
        QApplication.processEvents()

    def handle_git_error(self, error_msg):
        """处理Git操作错误"""
        self.log_message(f"操作失败: {error_msg}")

        # 检查是否是由于本地更改导致的分支切换失败
        if "Your local changes to the following files would be overwritten by checkout" in error_msg and "Please commit your changes or stash them before you switch branches" in error_msg:
            # 显示专门的错误对话框，提供解决方案
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setWindowTitle("分支切换失败")
            msg_box.setText("分支切换失败：您有未提交的更改")
            msg_box.setInformativeText(
                "您可以选择以下解决方案：\n\n"
                "1. 暂存更改并在切换分支后恢复\n"
                "2. 丢弃所有未提交的更改\n"
                "3. 提交更改后再切换分支"
            )
            msg_box.setDetailedText(error_msg)
            msg_box.exec_()
        # 检查是否是没有上游分支的错误
        elif "has no upstream branch" in error_msg:
            # 显示专门的错误对话框，提供解决方案
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setWindowTitle("推送失败")
            msg_box.setText("推送失败：当前分支没有设置上游分支")
            msg_box.setInformativeText(
                "您可以选择以下解决方案：\n\n"
                "1. 使用'推送并设置上游'按钮，自动设置上游分支\n"
                "2. 在终端中手动执行命令：git push --set-upstream origin <分支名>"
            )
            msg_box.setDetailedText(error_msg)
            msg_box.exec_()
        else:
            # 弹出普通错误对话框
            QMessageBox.critical(self, "操作失败", error_msg)

        # 重置所有按钮状态
        self.reset_all_buttons()

    def reset_all_buttons(self):
        """重置所有按钮到正常状态"""
        # 移除所有操作标记
        self.active_operations.clear()

        # 重置所有按钮
        self.set_button_loading(self.add_repo_btn, False)
        self.set_button_loading(self.remove_repo_btn, False)
        self.set_button_loading(self.refresh_btn, False)
        self.set_button_loading(self.refresh_status_btn, False)
        self.set_button_loading(self.stage_all_btn, False)
        self.set_button_loading(self.commit_btn, False)
        self.set_button_loading(self.commit_push_btn, False)
        self.set_button_loading(self.pull_btn, False)
        self.set_button_loading(self.push_btn, False)
        self.set_button_loading(self.push_set_upstream_btn, False)
        self.set_button_loading(self.create_branch_btn, False)
        self.set_button_loading(self.merge_btn, False)
        self.set_button_loading(self.clone_repo_btn, False)
        self.set_button_loading(self.delete_branch_btn, False)

        # 重置组合框
        self.set_combo_loading(self.branch_combo, False)
        self.set_combo_loading(self.merge_combo, False)

        # 更新按钮状态
        self.update_button_states()

    def refresh_branches(self):
        """刷新分支信息"""

        def on_branches_result(result):
            # 更新分支切换下拉框
            self.branch_combo.blockSignals(True)
            self.branch_combo.clear()

            local_branches = result.get('local_branches', [])
            remote_branches = result.get('remote_branches', [])
            active_branch = result.get('active_branch', None)

            # 添加本地分支
            for branch in local_branches:
                self.branch_combo.addItem(f"localctx: {branch}", branch)

            # 添加远程分支
            if remote_branches:
                self.branch_combo.insertSeparator(len(local_branches))  # 添加分隔符
                for branch in remote_branches:
                    self.branch_combo.addItem(f"remote: {branch}", branch)

            # 设置当前活动分支
            if active_branch:
                index = self.branch_combo.findData(active_branch)
                if index >= 0:
                    self.branch_combo.setCurrentIndex(index)

            self.branch_combo.blockSignals(False)

            # 更新合并下拉框（不包含当前分支）
            self.merge_combo.clear()
            current_branch = active_branch or ""
            all_branches = local_branches + remote_branches
            for branch in all_branches:
                if branch != current_branch:
                    self.merge_combo.addItem(branch, branch)

        self.execute_git_task("branches", callback=on_branches_result)

    def on_branch_activated(self, index):
        """当用户从下拉框中选择一个分支时触发"""
        # 避免重复操作
        if "switch_branch" in self.active_operations:
            return

        if index < 0:
            return

        # 获取存储的分支名数据
        branch_name = self.branch_combo.itemData(index)
        if not branch_name:
            # 如果没有数据，使用显示文本
            branch_name = self.branch_combo.itemText(index)
            # 移除前缀
            if branch_name.startswith("localctx: "):
                branch_name = branch_name[9:]  # 去掉 "localctx: " 前缀
            elif branch_name.startswith("remote: "):
                branch_name = branch_name[8:]  # 去掉 "remote: " 前缀

        self.switch_branch(branch_name)

    def switch_branch(self, branch_name):
        """切换分支 - 直接携带更改到新分支"""
        if not branch_name:
            return

        # 检查是否是远程分支
        combo_text = ""
        for i in range(self.branch_combo.count()):
            if self.branch_combo.itemData(i) == branch_name:
                combo_text = self.branch_combo.itemText(i)
                break

        self.active_operations.add("switch_branch")
        self.set_combo_loading(self.branch_combo, True)

        def on_checkout_result(result):
            self.log_message(result)
            self.refresh_current_repo()
            self.active_operations.discard("switch_branch")
            self.set_combo_loading(self.branch_combo, False)

        def on_checkout_error(error_msg):
            self.log_message(f"切换分支失败: {error_msg}")
            # 如果是由于本地更改导致的切换失败，显示详细信息
            if "Your local changes to the following files would be overwritten by checkout" in error_msg:
                QMessageBox.critical(self, "切换分支失败",
                                   f"无法将更改带到新分支，因为以下文件存在冲突：\n\n{error_msg}")
            self.active_operations.discard("switch_branch")
            self.set_combo_loading(self.branch_combo, False)
            # 刷新当前状态以确保UI同步
            self.refresh_current_repo()

        # 直接执行分支切换，尝试携带更改
        if combo_text.startswith("remote: "):
            # 这是一个远程分支，需要创建本地跟踪分支
            # 提取分支名，例如从 "origin/feature/new-feature" 提取 "feature/new-feature"
            if '/' in branch_name:
                # 去掉远程名称前缀（如 origin/）
                actual_branch_name = '/'.join(branch_name.split('/')[1:])
            else:
                actual_branch_name = branch_name

            # 检查是否已存在同名本地分支
            local_branch_exists = False
            try:
                for branch in self.current_repo.branches:
                    if branch.name == actual_branch_name:
                        local_branch_exists = True
                        break
            except:
                pass

            if not local_branch_exists:
                # 创建本地跟踪分支
                worker = GitWorker(self.current_repo_path, "checkout", "-b", actual_branch_name, branch_name)
            else:
                # 直接切换到已存在的本地分支
                worker = GitWorker(self.current_repo_path, "checkout", actual_branch_name)
        else:
            # 切换到本地分支
            worker = GitWorker(self.current_repo_path, "checkout", branch_name)

        worker.signals.result.connect(on_checkout_result)
        worker.signals.error.connect(on_checkout_error)
        self.threadpool.start(worker)

    def create_branch(self):
        """创建新分支"""
        if "create_branch" in self.active_operations:
            return

        branch_name = self.new_branch_input.text().strip()
        if not branch_name:
            self.log_message("请输入分支名称")
            return

        self.active_operations.add("create_branch")
        self.set_button_loading(self.create_branch_btn, True)

        def on_create_result(result):
            self.log_message(result)
            self.new_branch_input.clear()
            self.refresh_branches()
            self.active_operations.discard("create_branch")
            self.set_button_loading(self.create_branch_btn, False)

        self.execute_git_task("create_branch", branch_name, callback=on_create_result)

    def merge_branch(self):
        """合并分支"""
        if "merge_branch" in self.active_operations:
            return

        branch_name = self.merge_combo.currentText()
        if not branch_name:
            self.log_message("请选择要合并的分支")
            return

        current_branch = self.branch_combo.currentText()
        if branch_name == current_branch:
            self.log_message("不能合并当前分支到自身")
            return

        self.active_operations.add("merge_branch")
        self.set_button_loading(self.merge_btn, True)

        reply = QMessageBox.question(
            self, "确认合并",
            f"确定要将分支 '{branch_name}' 合并到当前分支 '{current_branch}' 听吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.No:
            self.active_operations.discard("merge_branch")
            self.set_button_loading(self.merge_btn, False)
            return

        def on_merge_result(result):
            self.log_message(result)
            self.refresh_current_repo()
            self.active_operations.discard("merge_branch")
            self.set_button_loading(self.merge_btn, False)

        self.execute_git_task("merge", branch_name, callback=on_merge_result)

    def refresh_status(self):
        """刷新文件状态"""
        if "refresh_status" in self.active_operations:
            return

        self.active_operations.add("refresh_status")
        self.set_button_loading(self.refresh_status_btn, True)

        def on_status_result(result):
            self.status_table.setRowCount(len(result))
            for row, (status, file_path, icon) in enumerate(result):
                self.status_table.setItem(row, 0, QTableWidgetItem(status))
                self.status_table.setItem(row, 1, QTableWidgetItem(file_path))
                self.status_table.setItem(row, 2, QTableWidgetItem(icon))
            self.active_operations.discard("refresh_status")
            self.set_button_loading(self.refresh_status_btn, False)

        self.execute_git_task("status", callback=on_status_result)

    def stage_all(self):
        """暂存所有文件"""
        if "stage_all" in self.active_operations:
            return

        self.active_operations.add("stage_all")
        self.set_button_loading(self.stage_all_btn, True)

        def on_add_result(result):
            self.log_message(result)
            self.refresh_status()
            self.active_operations.discard("stage_all")
            self.set_button_loading(self.stage_all_btn, False)

        self.execute_git_task("add", callback=on_add_result)

    def refresh_history(self):
        """刷新提交历史"""

        def on_log_result(result):
            self.history_list.clear()
            for commit in result:
                item_text = f"{commit['hash']} - {commit['message']}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, commit)
                self.history_list.addItem(item)

        self.execute_git_task("log", callback=on_log_result)

    def show_history_context_menu(self, position):
        """显示历史提交右键菜单"""
        item = self.history_list.itemAt(position)
        if not item:
            return

        commit_data = item.data(Qt.UserRole)
        if not commit_data:
            return

        menu = QMenu()

        # 查看详情
        view_action = QAction("查看详情", self)
        view_action.triggered.connect(lambda: self.show_commit_detail(item))
        menu.addAction(view_action)

        menu.addSeparator()

        # Cherry-pick 操作
        cherry_pick_action = QAction("Cherry-pick 到其他分支", self)
        cherry_pick_action.triggered.connect(lambda: self.cherry_pick_commit(commit_data))
        menu.addAction(cherry_pick_action)

        menu.exec_(self.history_list.mapToGlobal(position))

    def cherry_pick_commit(self, commit_data):
        """Cherry-pick 提交到其他分支"""
        if "cherry_pick" in self.active_operations:
            return

        # 获取所有分支
        if not self.current_repo:
            self.log_message("请先选择一个仓库")
            return

        branches = [branch.name for branch in self.current_repo.branches]
        current_branch = self.current_repo.active_branch.name

        # 移除当前分支
        other_branches = [branch for branch in branches if branch != current_branch]

        if not other_branches:
            self.log_message("没有其他分支可以 cherry-pick")
            return

        # 显示分支选择对话框
        branch, ok = QInputDialog.getItem(
            self,
            "选择目标分支",
            "选择要 cherry-pick 到的分支:",
            other_branches,
            0,
            False
        )

        if not ok or not branch:
            return

        # 确认操作
        reply = QMessageBox.question(
            self,
            "确认 Cherry-pick",
            f"确定要将提交 {commit_data['hash']} cherry-pick 到分支 '{branch}' 吗？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.No:
            return

        self.active_operations.add("cherry_pick")
        self.log_message(f"正在 cherry-pick 提交 {commit_data['hash']} 到分支 {branch}...")

        def on_cherry_pick_result(result):
            self.log_message(result)
            self.active_operations.discard("cherry_pick")
            # 刷新当前分支信息
            self.refresh_current_repo()

        # 执行 cherry-pick 操作
        self.execute_git_task("cherry_pick", commit_data['full_hash'], callback=on_cherry_pick_result)

    def show_commit_detail(self, item):
        """显示提交详情"""
        if "show_commit" in self.active_operations:
            return

        commit_data = item.data(Qt.UserRole)

        self.active_operations.add("show_commit")

        # 禁用历史列表以防止重复双击
        self.history_list.setEnabled(False)
        # 更新选中项的文本显示加载状态
        item.setText(item.text() + " (加载中...)")

        def on_commit_detail_result(result):
            dialog = CommitDetailDialog(result, self)
            dialog.exec_()
            self.active_operations.discard("show_commit")
            # 恢复历史列表状态
            self.history_list.setEnabled(True)
            # 恢复项目文本
            original_text = item.text().replace(" (加载中...)", "")
            item.setText(original_text)

        def on_error(error_msg):
            self.log_message(f"获取提交详情失败: {error_msg}")
            # 即使出错也显示基本信息
            basic_info = {
                'commit': commit_data,
                'files_changed': []
            }
            dialog = CommitDetailDialog(basic_info, self)
            dialog.exec_()
            self.active_operations.discard("show_commit")
            # 恢复历史列表状态
            self.history_list.setEnabled(True)
            # 恢复项目文本
            original_text = item.text().replace(" (加载中...)", "")
            item.setText(original_text)

        worker = GitWorker(self.current_repo_path, "show_commit", commit_data['full_hash'])
        worker.signals.result.connect(on_commit_detail_result)
        worker.signals.error.connect(on_error)
        self.threadpool.start(worker)

    def commit(self):
        """提交"""
        if "commit" in self.active_operations:
            return

        message = self.commit_message.toPlainText().strip()
        if not message:
            self.log_message("请输入提交信息")
            return

        self.active_operations.add("commit")
        self.set_button_loading(self.commit_btn, True)

        def on_commit_result(result):
            self.log_message(result)
            self.commit_message.clear()
            self.refresh_status()
            self.refresh_history()
            self.active_operations.discard("commit")
            self.set_button_loading(self.commit_btn, False)

        self.execute_git_task("commit", message, callback=on_commit_result)

    def commit_and_push(self):
        """提交并推送（包含add .）"""
        if "commit_and_push" in self.active_operations:
            return

        self.active_operations.add("commit_and_push")
        self.set_button_loading(self.commit_push_btn, True)

        def on_add_result(result):
            self.log_message(result)

            def on_commit_result(commit_result):
                self.log_message(commit_result)
                self.commit_message.clear()
                self.refresh_status()
                self.refresh_history()

                # 提交成功后推送
                def on_push_result(push_result):
                    self.log_message(push_result)
                    self.active_operations.discard("commit_and_push")
                    self.set_button_loading(self.commit_push_btn, False)

                self.execute_git_task("push", callback=on_push_result)

            message = self.commit_message.toPlainText().strip()
            if not message:
                self.log_message("请输入提交信息")
                self.active_operations.discard("commit_and_push")
                self.set_button_loading(self.commit_push_btn, False)
                return

            self.execute_git_task("commit", message, callback=on_commit_result)

        # 先执行 add .
        self.execute_git_task("add", callback=on_add_result)

    def pull(self):
        """拉取"""
        if "pull" in self.active_operations:
            return

        if not self.current_repo_path:
            self.log_message("请先选择一个仓库")
            return

        # 获取拉取选项
        rebase = self.pull_rebase_checkbox.isChecked()
        prune = self.pull_prune_checkbox.isChecked()

        self.active_operations.add("pull")
        self.set_button_loading(self.pull_btn, True)

        def on_pull_result(result):
            self.log_message(result)
            self.refresh_current_repo()
            self.active_operations.discard("pull")
            self.set_button_loading(self.pull_btn, False)

        # 传递拉取选项参数
        self.execute_git_task("pull", rebase, prune, callback=on_pull_result)

    def push(self, set_upstream=False):
        """推送"""
        if "push" in self.active_operations:
            return

        if not self.current_repo_path:
            self.log_message("请先选择一个仓库")
            return

        self.active_operations.add("push")
        self.set_button_loading(self.push_btn, True)
        self.set_button_loading(self.push_set_upstream_btn, True)

        def on_push_result(result):
            self.log_message(result)
            self.active_operations.discard("push")
            self.set_button_loading(self.push_btn, False)
            self.set_button_loading(self.push_set_upstream_btn, False)

        # 根据参数决定使用哪种推送方式
        operation = "push_with_upstream" if set_upstream else "push"
        self.execute_git_task(operation, callback=on_push_result)

    def show_file_diff(self, item):
        """双击文件显示差异"""
        # 获取行号
        row = item.row()
        # 获取文件状态和路径
        status_item = self.status_table.item(row, 0)
        file_item = self.status_table.item(row, 1)

        if not status_item or not file_item:
            return

        status = status_item.text()
        file_path = file_item.text()

        # 只有未暂存和修改的文件才显示差异
        if status not in ["未暂存", "修改", "未跟踪"]:
            self.log_message(f"文件 {file_path} 状态为 {status}，无需显示差异")
            return

        def on_diff_result(diff_content):
            dialog = DiffDialog(file_path, diff_content, self)
            dialog.exec_()

        def on_diff_error(error_msg):
            self.log_message(f"获取文件差异失败: {error_msg}")
            QMessageBox.critical(self, "错误", f"获取文件差异失败: {error_msg}")

        # 执行差异获取任务
        worker = GitWorker(self.current_repo_path, "diff", file_path)
        worker.signals.result.connect(on_diff_result)
        worker.signals.error.connect(on_diff_error)
        self.threadpool.start(worker)

    def show_status_context_menu(self, position):
        """显示文件状态右键菜单"""
        item = self.status_table.itemAt(position)
        if not item:
            return

        # 获取行号
        row = item.row()
        # 获取文件状态和路径
        status_item = self.status_table.item(row, 0)
        file_item = self.status_table.item(row, 1)

        if not status_item or not file_item:
            return

        status = status_item.text()
        file_path = file_item.text()

        # 创建菜单
        menu = QMenu()

        # 只有未暂存和修改的文件才能取消变更
        if status in ["未暂存", "修改"]:
            cancel_action = QAction("取消变更", self)
            cancel_action.triggered.connect(lambda: self.cancel_file_changes(file_path))
            menu.addAction(cancel_action)
            menu.addSeparator()

        # 所有文件都可以查看差异
        diff_action = QAction("查看差异", self)
        diff_action.triggered.connect(lambda: self.show_file_diff_at_row(row))
        menu.addAction(diff_action)

        menu.exec_(self.status_table.mapToGlobal(position))

    def show_file_diff_at_row(self, row):
        """在指定行显示文件差异"""
        # 获取文件状态和路径
        status_item = self.status_table.item(row, 0)
        file_item = self.status_table.item(row, 1)

        if not status_item or not file_item:
            return

        status = status_item.text()
        file_path = file_item.text()

        # 只有未暂存和修改的文件才显示差异
        if status not in ["未暂存", "修改", "未跟踪"]:
            self.log_message(f"文件 {file_path} 状态为 {status}，无需显示差异")
            return

        def on_diff_result(diff_content):
            dialog = DiffDialog(file_path, diff_content, self)
            dialog.exec_()

        def on_diff_error(error_msg):
            self.log_message(f"获取文件差异失败: {error_msg}")
            QMessageBox.critical(self, "错误", f"获取文件差异失败: {error_msg}")

        # 执行差异获取任务
        worker = GitWorker(self.current_repo_path, "diff", file_path)
        worker.signals.result.connect(on_diff_result)
        worker.signals.error.connect(on_diff_error)
        self.threadpool.start(worker)

    def cancel_file_changes(self, file_path):
        """取消文件变更"""
        reply = QMessageBox.question(
            self,
            "确认取消变更",
            f"确定要取消文件 '{file_path}' 的变更吗？这将丢弃所有未暂存的修改。",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            def on_checkout_result(result):
                self.log_message(result)
                self.refresh_status()

            def on_checkout_error(error_msg):
                self.log_message(f"取消文件变更失败: {error_msg}")
                QMessageBox.critical(self, "错误", f"取消文件变更失败: {error_msg}")

            # 执行取消变更任务
            worker = GitWorker(self.current_repo_path, "checkout_file", file_path)
            worker.signals.result.connect(on_checkout_result)
            worker.signals.error.connect(on_checkout_error)
            self.threadpool.start(worker)

    def clone_repo(self):
        """克隆远程仓库"""
        if "clone_repo" in self.active_operations:
            return

        # 创建克隆对话框
        dialog = CloneDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.active_operations.add("clone_repo")
            self.set_button_loading(self.clone_repo_btn, True)

            # 创建克隆工作线程
            worker = CloneWorker(dialog.repo_url, dialog.local_path, dialog.clone_target_path)
            worker.signals.result.connect(self.on_clone_success)
            worker.signals.error.connect(self.on_clone_error)
            worker.signals.progress.connect(self.log_message)
            self.threadpool.start(worker)

    def on_clone_success(self, result):
        """克隆成功回调"""
        self.log_message(result)
        # 将克隆的仓库添加到配置中
        repo_path = result.split(": ")[-1]  # 提取路径
        if repo_path not in self.repo_paths:
            self.repo_paths.append(repo_path)
            self.save_config()
            self.update_repo_list()
        self.active_operations.discard("clone_repo")
        self.set_button_loading(self.clone_repo_btn, False)

    def on_clone_error(self, error_msg):
        """克隆失败回调"""
        self.log_message(f"克隆失败: {error_msg}")
        QMessageBox.critical(self, "克隆失败", f"克隆仓库时出错: {error_msg}")
        self.active_operations.discard("clone_repo")
        self.set_button_loading(self.clone_repo_btn, False)

    def delete_branch(self):
        """删除分支"""
        if not self.current_repo_path:
            self.log_message("请先选择一个仓库")
            return

        # 获取所有本地分支
        if not self.current_repo:
            return

        local_branches = [branch.name for branch in self.current_repo.branches]
        active_branch = self.current_repo.active_branch.name

        # 移除当前分支（不能删除当前分支）
        branches_to_delete = [branch for branch in local_branches if branch != active_branch]

        if not branches_to_delete:
            self.log_message("没有可以删除的分支")
            return

        # 让用户选择要删除的分支
        branch_to_delete, ok = QInputDialog.getItem(
            self,
            "删除分支",
            "选择要删除的分支:",
            branches_to_delete,
            0,
            False
        )

        if not ok or not branch_to_delete:
            return

        # 询问是否强制删除
        force_delete = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除分支 '{branch_to_delete}' 吗？\n\n"
            "点击'是'进行普通删除，点击'否'进行强制删除，点击'取消'取消操作。",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
        )

        if force_delete == QMessageBox.Cancel:
            return

        force = (force_delete == QMessageBox.No)

        def on_delete_branch_result(result):
            self.log_message(result)
            # 刷新分支信息
            self.refresh_branches()

        def on_delete_branch_error(error_msg):
            self.log_message(f"删除分支失败: {error_msg}")
            QMessageBox.critical(self, "错误", f"删除分支失败: {error_msg}")

        # 执行删除分支任务
        worker = GitWorker(self.current_repo_path, "delete_branch", branch_to_delete, force)
        worker.signals.result.connect(on_delete_branch_result)
        worker.signals.error.connect(on_delete_branch_error)
        self.threadpool.start(worker)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # 设置应用程序样式以提高兼容性
    app.setStyle("Fusion")
    manager = GitManager()
    manager.show()
    sys.exit(app.exec_())
