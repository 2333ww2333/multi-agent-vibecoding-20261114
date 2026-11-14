import Welcome from '@ant-design/x/es/welcome';
import { RobotOutlined } from '@ant-design/icons';
import { Kbd } from '../../../shared/ui/Kbd';

export function EmptyState() {
  return (
    <div className="empty">
      <Welcome
        variant="borderless"
        icon={<RobotOutlined />}
        title="要做什么？"
        description="说清目标与验收标准；多步骤任务我会先出计划，再逐条推进。"
      />

      <div className="empty__hints">
        <span>
          <Kbd>⌘K</Kbd> 命令面板
        </span>
        <span>
          <Kbd>↵</Kbd> 发送
        </span>
        <span>
          <Kbd>Esc</Kbd> 停止 / 关闭
        </span>
      </div>
    </div>
  );
}
