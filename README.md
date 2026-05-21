# 2026sp-SMarDuino
A pick-and-place machine


## Contributors
- [@YahyaBoussaadia] https://github.com/YahyaBoussaadia
- [@alaae] https://github.com/alaaae
- [@khlilinfo] https://github.com/khlilinfo
- [@zeinebsellami] https://github.com/zeinebsellami
- [@yosrhall] https://github.com/yosrhall

## Electronics Assembly

The final electronic architecture of the project has been assembled.

- Arduino Mega connected to the CNC Shield
- Five stepper motor drivers installed and configured
- X, Y, Z and rotation motors connected
- Limit switches added for homing
- Vacuum pump controlled using a transistor switching circuit
- The transistor allows the Arduino to safely activate and deactivate the pump
- Buck converter used to provide a stable power supply for the pump
- Camera system connected for component alignment
- Common ground shared between all electronic modules

This setup allows the machine to perform the full pick-and-place workflow:
homing, component pickup, camera alignment, and PCB placement.

<img width="1451" height="770" alt="Capture d&#39;écran 2026-05-21 030021" src="https://github.com/user-attachments/assets/d6a49caf-2faa-4bd9-badd-0593c9f2a8b9" />
