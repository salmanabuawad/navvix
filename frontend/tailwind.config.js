/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        header:  '#2E62A2',
        sidebar: '#2F4D52',
        'sidebar-hover':   '#3D6971',
        'sidebar-active':  '#3D6971',
        accent:  '#2196F3',
        'accent-hover': '#1976D2',
        danger:  '#F44336',
        bg:      '#F0F4F7',
        panel:   '#FFFFFF',
        border:  '#D1D9E0',
        muted:   '#6C757D',
        text:    '#1A2332',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
