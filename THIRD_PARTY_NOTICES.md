# Dependencies and third-party notices

No Keithley/Tektronix copyrighted manuals, drivers or other vendor source files
are embedded in this repository. They are **linked**, not copied.

| Dependency | Usage | License / source |
| --- | --- | --- |
| [pySerial](https://github.com/pyserial/pyserial) | Optional at runtime unless using UART; required by installation metadata | BSD-3-Clause; see upstream LICENSE.txt |
| [Matplotlib](https://matplotlib.org/stable/project/license.html) | Optional charts only (`pip install -e '.[plot]'`) | Matplotlib License (PSF/BSD-style) |

Dependencies, their optional transitive dependencies and applicable license terms
remain under their respective upstream licenses. The project's MIT license
covers original source and documentation only, not third-party software.
