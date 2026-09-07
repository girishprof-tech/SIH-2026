import unittest
from simulation import run

class SimulationSafetyTests(unittest.TestCase):
    def test_all_tasks_complete_without_collisions(self):
        data = run(max_ticks=1000, output_path='test_sim_output.json')
        self.assertTrue(data['meta']['all_completed'])
        frames = data['frames']
        for frame in frames:
            active = [r for r in frame['robots'] if not r['completed']]
            positions = [(r['x'], r['y']) for r in active]
            self.assertEqual(len(positions), len(set(positions)))

        for prev, cur in zip(frames, frames[1:]):
            p = {r['id']: (r['x'], r['y']) for r in prev['robots'] if not r['completed']}
            c = {r['id']: (r['x'], r['y']) for r in cur['robots'] if not r['completed']}
            common = set(p) & set(c)
            ids = sorted(common)
            for i, a in enumerate(ids):
                for b in ids[i+1:]:
                    self.assertFalse(p[a] == c[b] and p[b] == c[a] and p[a] != p[b])

if __name__ == '__main__':
    unittest.main()
