import React from 'react'
import ReactDOM from 'react-dom/client'
import { LocaleProvider } from '@douyinfe/semi-ui'
import zh_CN from '@douyinfe/semi-ui/lib/es/locale/source/zh_CN'
import App from './App.jsx'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <LocaleProvider locale={zh_CN}>
      <App />
    </LocaleProvider>
  </React.StrictMode>
)
