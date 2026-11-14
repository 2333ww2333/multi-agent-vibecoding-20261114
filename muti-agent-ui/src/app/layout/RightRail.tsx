import { Button, Tabs, Tooltip } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import { PlanPanel } from '../../features/workbench/PlanPanel';
import { BoardPanel } from '../../features/workbench/BoardPanel';
import { TeamPanel } from '../../features/workbench/TeamPanel';
import { InboxPanel } from '../../features/workbench/InboxPanel';
import { FilesPanel } from '../../features/workbench/FilesPanel';
import { UsagePanel } from '../../features/workbench/UsagePanel';
import { useUiStore } from '../../shared/stores/ui';
import '../../features/workbench/workbench.css';

export function RightRail() {
  const setRailOpen = useUiStore((state) => state.setRailOpen);

  return (
    <aside className="shell__rail">
      <Tabs
        size="small"
        defaultActiveKey="plan"
        className="rail-tabs"
        tabBarExtraContent={
          <Tooltip title="收起面板">
            <Button
              type="text"
              size="small"
              aria-label="收起右侧面板"
              icon={<CloseOutlined />}
              onClick={() => setRailOpen(false)}
            />
          </Tooltip>
        }
        items={[
          { key: 'plan', label: '计划', children: <PlanPanel /> },
          { key: 'board', label: '看板', children: <BoardPanel /> },
          { key: 'team', label: '队友', children: <TeamPanel /> },
          { key: 'inbox', label: '收件', children: <InboxPanel /> },
          { key: 'files', label: '文件', children: <FilesPanel /> },
          { key: 'usage', label: '账单', children: <UsagePanel /> },
        ]}
      />
    </aside>
  );
}
