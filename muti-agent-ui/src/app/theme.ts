import { theme, type ThemeConfig } from 'antd';

/** 与 09 章的设计 token 一一对应；组件只消费这里，不写死颜色。 */
export function buildTheme(dark: boolean): ThemeConfig {
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: '#4D6BFE',
      colorBgBase: dark ? '#0B0B0C' : '#FFFFFF',
      colorTextBase: dark ? '#EDEDEF' : '#18181B',
      colorBorder: dark ? '#26262A' : '#E7E7E8',
      colorBorderSecondary: dark ? '#1F1F23' : '#F0F0F1',
      colorTextSecondary: dark ? '#A1A1AA' : '#52525B',
      colorTextTertiary: dark ? '#6E6E76' : '#8A8A93',
      borderRadius: 6,
      borderRadiusLG: 8,
      borderRadiusSM: 4,
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
      fontFamilyCode:
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, 'Cascadia Code', Consolas, monospace",
      fontSize: 14,
      controlHeight: 32,
      wireframe: false,
      boxShadow: 'none',
      boxShadowSecondary: dark ? '0 8px 24px rgba(0,0,0,.28)' : '0 8px 24px rgba(0,0,0,.10)',
    },
    components: {
      Button: { primaryShadow: 'none', defaultShadow: 'none', fontWeight: 400 },
      Card: { paddingLG: 16 },
      Modal: { borderRadiusLG: 10 },
      Tooltip: { borderRadius: 6 },
      Tabs: { horizontalItemPadding: '10px 0', inkBarColor: '#4D6BFE' },
      Table: { headerBg: 'transparent', rowHoverBg: dark ? '#1A1A1D' : '#F4F4F5' },
      Input: { activeShadow: '0 0 0 2px rgba(77,107,254,.12)' },
      Segmented: { itemSelectedBg: dark ? '#1A1A1D' : '#F4F4F5' },
    },
  };
}
