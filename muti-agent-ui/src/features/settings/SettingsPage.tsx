import { useState } from 'react';
import { Button, Segmented, App, Input, Tag, Popconfirm } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '../../shared/api/endpoints';
import { API_BASE, USE_MOCK, authToken, setAuthToken } from '../../shared/api/client';
import { useUiStore, type ThemeMode } from '../../shared/stores/ui';
import { resetMock } from '../../shared/api/mock';
import './settings.css';

export function SettingsPage() {
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [token, setToken] = useState(authToken());
  const [wsInput, setWsInput] = useState('');
  const [switching, setSwitching] = useState(false);

  const metaQuery = useQuery({ queryKey: ['meta'], queryFn: () => api.meta() });
  const meta = metaQuery.data;
  const wsQuery = useQuery({
    queryKey: ['workspace'],
    queryFn: () => api.getWorkspace(),
    enabled: !USE_MOCK,
  });
  const ws = wsQuery.data;

  const doSwitch = async () => {
    const target = wsInput.trim();
    if (!target) {
      message.warning('请输入工作区的绝对路径');
      return;
    }
    setSwitching(true);
    try {
      const next = await api.switchWorkspace(target);
      message.success(`工作区已切换到 ${next.root}`);
      setWsInput('');
      await queryClient.invalidateQueries({ queryKey: ['workspace'] });
      await queryClient.invalidateQueries({ queryKey: ['meta'] });
      queryClient.invalidateQueries({ queryKey: ['files'] });
    } catch (error) {
      const text =
        error instanceof Error ? error.message : '切换失败，请检查路径';
      message.error(text);
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div className="settings">
      <div className="settings__inner">
        <header className="settings__head">
          <h1>设置</h1>
          <Button type="text" onClick={() => navigate('/')}>
            返回对话
          </Button>
        </header>

        <section className="settings__section">
          <h2>外观</h2>
          <div className="settings__row">
            <span>主题</span>
            <Segmented
              value={theme}
              onChange={(value) => setTheme(value as ThemeMode)}
              options={[
                { value: 'light', label: '浅色' },
                { value: 'dark', label: '深色' },
              ]}
            />
          </div>
        </section>

        <section className="settings__section">
          <h2>连接</h2>
          <div className="settings__row">
            <span>模式</span>
            <Tag variant="filled" color={USE_MOCK ? 'gold' : 'green'}>
              {USE_MOCK ? 'Mock（前端内置数据）' : 'Live（真实 BFF）'}
            </Tag>
          </div>
          <div className="settings__row">
            <span>API Base</span>
            <code>{API_BASE || '(同源 /api)'}</code>
          </div>
          <p className="muted">
            切换模式需要改 <code>.env.development</code> 里的 <code>VITE_USE_MOCK</code> 与
            <code>VITE_API_BASE_URL</code>，然后重启 dev server。
          </p>

          <div className="settings__row settings__row--stack">
            <span>访问令牌（仅当前标签页）</span>
            <Input.Password
              value={token}
              placeholder="MA_WEB_TOKEN，留空表示未配置"
              onChange={(event) => setToken(event.target.value)}
            />
            <Button
              size="small"
              onClick={() => {
                setAuthToken(token.trim());
                message.success(token.trim() ? '令牌已保存到当前标签页' : '令牌已清除');
              }}
            >
              保存
            </Button>
          </div>
        </section>

        {ws && (
          <section className="settings__section">
            <h2>工作区</h2>
            <div className="settings__row">
              <span>当前根目录</span>
              <code>{ws.root}</code>
              <Tag variant="filled" color={ws.source === 'saved' ? 'green' : 'default'}>
                {ws.source === 'saved'
                  ? '界面选择'
                  : ws.source === 'env'
                    ? '环境变量'
                    : '默认'}
              </Tag>
            </div>
            <p className="muted">
              Agent 的文件读写都限制在这个目录内（沙箱）。切换后立即生效并持久保存，
              重启后端也会沿用；知识库与模型路径不受影响。
            </p>
            {ws.busy ? (
              <p className="muted">有任务正在运行，结束后才能切换工作区。</p>
            ) : (
              <div className="settings__row settings__row--stack">
                <span>切换到新目录（绝对路径，不存在会自动创建）</span>
                <Input
                  value={wsInput}
                  placeholder="例如 D:\\Demos\\AgriWorkspace"
                  allowClear
                  onChange={(event) => setWsInput(event.target.value)}
                  onPressEnter={doSwitch}
                />
                <Popconfirm
                  title="切换工作区？"
                  description="切换后 Agent 的文件读写会定位到新目录。"
                  okText="切换"
                  cancelText="取消"
                  onConfirm={doSwitch}
                >
                  <Button type="primary" size="small" loading={switching}>
                    切换工作区
                  </Button>
                </Popconfirm>
              </div>
            )}
          </section>
        )}

        <section className="settings__section">
          <h2>运行环境</h2>
          <dl className="settings__defs">
            <div>
              <dt>版本</dt>
              <dd>{meta?.app_version ?? '—'}</dd>
            </div>
            <div>
              <dt>模型</dt>
              <dd>{meta?.model ?? '—'}</dd>
            </div>
            <div>
              <dt>工作区</dt>
              <dd>{meta?.workspace.root ?? '—'}</dd>
            </div>
            <div>
              <dt>工具</dt>
              <dd>
                {meta
                  ? `流式 ${meta.capabilities.stream ? '✓' : '✗'} · 审批 ${
                      meta.capabilities.approvals ? '✓' : '✗'
                    } · 团队 ${meta.capabilities.team ? '✓' : '✗'}`
                  : '—'}
              </dd>
            </div>
          </dl>
        </section>

        {USE_MOCK && (
          <section className="settings__section">
            <h2>演示数据</h2>
            <p className="muted">
              重置会清空 Mock 的会话、消息、看板、队友与账单，并恢复初始演示数据。
            </p>
            <Button
              danger
              onClick={() => {
                resetMock();
                queryClient.clear();
                message.success('已重置 Mock 数据');
              }}
            >
              重置演示数据
            </Button>
          </section>
        )}
      </div>
    </div>
  );
}
