import '@fontsource-variable/plus-jakarta-sans'
import './styles/tokens.css'
import './styles/base.css'
import './styles/app.css'
import './styles/screens.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './App'

const root = document.getElementById('root')
if (root === null) throw new Error('index.html has no #root element')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
