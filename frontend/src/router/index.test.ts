import { describe, expect, it } from 'vitest'

import { router } from '@/router'

// Optimization V2 (M1.1)：新路由骨架与 legacy redirect 契约。
describe('router redirects (M1.1)', () => {
  async function expectRedirect(from: string, to: string) {
    await router.push(from)
    await router.isReady()
    expect(router.currentRoute.value.fullPath).toBe(to)
  }

  it('redirects legacy /cases/:caseId to investigation overview preserving query', async () => {
    await expectRedirect(
      '/cases/case-1?tab=evidence',
      '/investigations/case-1/overview?tab=evidence',
    )
  }, 20000)

  it('redirects bare /cases to /investigations', async () => {
    await expectRedirect('/cases', '/investigations')
  })

  it('redirects /investigations/:caseId to overview', async () => {
    await expectRedirect('/investigations/case-1', '/investigations/case-1/overview')
  })

  it('redirects legacy admin routes to /admin/*', async () => {
    await expectRedirect('/approvals', '/admin/approvals')
    await expectRedirect('/reviews', '/admin/reviews')
    await expectRedirect('/memories', '/admin/memories')
    await expectRedirect('/security', '/admin/security')
    await expectRedirect('/observability', '/admin/observability')
    await expectRedirect('/resilience', '/admin/resilience')
  })

  it('redirects /admin to first admin tab and /dashboard to home', async () => {
    await expectRedirect('/admin', '/admin/approvals')
    await expectRedirect('/dashboard', '/')
  })
})
