import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './tokens.css'
import './app.css'

const theme = localStorage.getItem('ac-theme') ?? 'light'
document.documentElement.dataset.theme = theme

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
