import tkinter as tk
from tkinter import messagebox
import subprocess
import os

def submit_lots():
    lot_input = entry_lots.get().strip()
    
    # Validate input
    if not lot_input or lot_input.startswith("e.g"):
        messagebox.showerror(
            "Error", 
            "Please enter at least one Lot ID!"
        )
        return
    
    # Clean lot IDs
    lots = [lot.strip() for lot in lot_input.split(',')]
    lot_string = ','.join(lots)
    
    # Show loading
    status_label.config(
        text="⏳ Processing... Please wait...", 
        fg="orange"
    )
    submit_btn.config(state="disabled")
    root.update()
    
    try:
        # ============ YOUR CORRECT PATHS ============
        knime_exe = (
            r"C:\Program Files\KNIME"
            r"\KNIME Analytics Platform\knime.exe"
        )
        
        workflow = (
            r"C:\Users\nazrinurnisa\knime-workspace"
            r"\FCT_APC_MAPPING RUN  SEARCH 2LOT GUI"
        )
        
        output_file = (
            r"C:\Users\nazrinurnisa\Desktop\COMBINE ALL.xlsx"
        )
        # ============================================
        
        # Delete old file first (clean run)
        if os.path.exists(output_file):
            os.remove(output_file)
            print("Old file deleted ✅")
        
        # Run KNIME workflow
        cmd = [
            knime_exe,
            "-nosplash",
            "-noexit",
            "--launcher.suppressErrors",
            "-application",
            "org.knime.product.KNIME_BATCH_APPLICATION",
            "-workflowDir", workflow,
            "-workflow.variable",
            f"search_lot,{lot_string},String"
        ]
        
        print(f"Running KNIME with lots: {lot_string}")
        
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        
        # Check output file exists
        if os.path.exists(output_file):
            status_label.config(
                text="✅ Done! Opening your Excel file...",
                fg="green"
            )
            root.update()
            # Auto open Excel file
            os.startfile(output_file)
        else:
            status_label.config(
                text="⚠️ Done but file not found! Check Desktop",
                fg="orange"
            )
            
    except subprocess.CalledProcessError as e:
        status_label.config(
            text="❌ KNIME Error! Check workflow",
            fg="red"
        )
        messagebox.showerror(
            "KNIME Error",
            f"KNIME workflow failed!\n\nDetails:\n{str(e)}"
        )
    except FileNotFoundError:
        status_label.config(
            text="❌ KNIME not found!",
            fg="red"
        )
        messagebox.showerror(
            "File Not Found",
            f"KNIME.exe not found!\n\n"
            f"Check path:\n{knime_exe}"
        )
    except Exception as e:
        status_label.config(
            text="❌ Error occurred!",
            fg="red"
        )
        messagebox.showerror(
            "Error", 
            f"Something went wrong!\n\n{str(e)}"
        )
    finally:
        # Re-enable button
        submit_btn.config(state="normal")


def clear_input():
    entry_lots.delete(0, tk.END)
    entry_lots.insert(0, "e.g. VA614483,VA620896,VA614484")
    entry_lots.config(fg="grey")
    status_label.config(text="")


def on_entry_click(event):
    if entry_lots.get().startswith("e.g"):
        entry_lots.delete(0, tk.END)
        entry_lots.config(fg="black")


def on_focus_out(event):
    if entry_lots.get() == "":
        entry_lots.insert(0, "e.g. VA614483,VA620896,VA614484")
        entry_lots.config(fg="grey")


# ============ GUI DESIGN ============
root = tk.Tk()
root.title("LOT Query Tool - FCT APC Mapping")
root.geometry("520x430")
root.configure(bg="#f0f0f0")
root.resizable(False, False)

# ── Title ──
tk.Label(
    root,
    text="🔍 LOT Query Tool",
    font=("Arial", 20, "bold"),
    bg="#f0f0f0",
    fg="#2c3e50"
).pack(pady=15)

# ── Subtitle ──
tk.Label(
    root,
    text="APC + FCT + Python Combined Report",
    font=("Arial", 10),
    bg="#f0f0f0",
    fg="#7f8c8d"
).pack()

# ── Divider ──
tk.Frame(
    root,
    height=2,
    bg="#bdc3c7"
).pack(fill="x", padx=20, pady=10)

# ── Input Label ──
tk.Label(
    root,
    text="Enter Lot IDs (comma separated):",
    font=("Arial", 11, "bold"),
    bg="#f0f0f0",
    fg="#2c3e50"
).pack(pady=5)

# ── Example Label ──
tk.Label(
    root,
    text="Example: VA614483,VA620896,VA614484",
    font=("Arial", 9),
    bg="#f0f0f0",
    fg="#95a5a6"
).pack()

# ── Input Box ──
entry_lots = tk.Entry(
    root,
    width=45,
    font=("Arial", 11),
    relief="solid",
    bd=1,
    fg="grey"
)
entry_lots.pack(pady=8, ipady=6)
entry_lots.insert(0, "e.g. VA614483,VA620896,VA614484")
entry_lots.bind("<FocusIn>", on_entry_click)
entry_lots.bind("<FocusOut>", on_focus_out)

# ── Buttons Frame ──
btn_frame = tk.Frame(root, bg="#f0f0f0")
btn_frame.pack(pady=10)

# ── Submit Button ──
submit_btn = tk.Button(
    btn_frame,
    text="▶  GENERATE EXCEL",
    font=("Arial", 12, "bold"),
    bg="#27ae60",
    fg="white",
    padx=20,
    pady=10,
    relief="flat",
    cursor="hand2",
    command=submit_lots
)
submit_btn.grid(row=0, column=0, padx=5)

# ── Clear Button ──
tk.Button(
    btn_frame,
    text="🗑  CLEAR",
    font=("Arial", 12),
    bg="#e74c3c",
    fg="white",
    padx=20,
    pady=10,
    relief="flat",
    cursor="hand2",
    command=clear_input
).grid(row=0, column=1, padx=5)

# ── Divider ──
tk.Frame(
    root,
    height=2,
    bg="#bdc3c7"
).pack(fill="x", padx=20, pady=5)

# ── Status Label ──
status_label = tk.Label(
    root,
    text="",
    font=("Arial", 10),
    bg="#f0f0f0",
    fg="green",
    wraplength=450
)
status_label.pack(pady=10)

# ── Footer ──
tk.Label(
    root,
    text="📁 Output: Desktop → COMBINE ALL.xlsx",
    font=("Arial", 9),
    bg="#f0f0f0",
    fg="#95a5a6"
).pack(side="bottom", pady=10)

root.mainloop()