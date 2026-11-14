import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
// 深路径导入：X 的 index 会连带引入 CodeHighlighter -> react-syntax-highlighter，
// 当前依赖树里 highlight.js 缺失会让打包直接失败。只引组件本身即可。
import XProvider from '@ant-design/x/es/x-provider';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { buildTheme } from './theme';
import { useUiStore } from '../shared/stores/ui';

export function Providers({ children }: { children: ReactNode }) {
  const themeMode = useUiStore((state) => state.theme);

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
  }, [themeMode]);

  return (
    <ConfigProvider theme={buildTheme(themeMode === 'dark')} locale={zhCN}>
      <XProvider>
        <AntdApp>
          <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
        </AntdApp>
      </XProvider>
    </ConfigProvider>
  );
}
